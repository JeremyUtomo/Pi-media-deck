using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using Microsoft.Extensions.Logging;
using PcCompanion.Audio;
using PcCompanion.Discord;
using PcCompanion.Media;

namespace PcCompanion.Server;

/// <summary>Tracks connected decks, pushes state to them and runs their commands.</summary>
public sealed class DeckHub
{
    private const int MaxIncomingMessageBytes = 64 * 1024;

    // Commands run one at a time on their own worker, never in a receive loop: a Core Audio call can hang for
    // minutes (seen with the Windows audio service restarting), and a blocked receive loop stops answering the
    // deck's pings, so the deck drops the connection and the command is lost. A command that waited this long is
    // skipped instead of run late (otherwise a hang ends with a burst of stale output switches).
    private static readonly TimeSpan CommandMaxAge = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan SlowCommand = TimeSpan.FromSeconds(3);

    private readonly Channel<(DeckClient Client, string Text, long Queued)> _commands =
        Channel.CreateBounded<(DeckClient, string, long)>(new BoundedChannelOptions(128)
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
        });

    private readonly MediaService _media;
    private readonly AudioService _audio;
    private readonly DiscordService _discord;
    private readonly ILogger<DeckHub> _log;
    private readonly ConcurrentDictionary<DeckClient, byte> _clients = new();
    private string? _lastMediaSummary;

    public DeckHub(MediaService media, AudioService audio, DiscordService discord, ILogger<DeckHub> log)
    {
        _media = media;
        _audio = audio;
        _discord = discord;
        _log = log;

        _discord.StateChanged += state =>
        {
            _log.LogInformation("Discord: {Status}{Call}", state.Status, state.InCall ? $", in call '{state.Channel}'" : "");
            Broadcast(Protocol.DiscordState(state));
        };

        _media.ArtChanged += art => Broadcast(Protocol.MediaArt(art));
        _media.StateChanged += state =>
        {
            // Timeline updates arrive every few seconds; only log track and play/pause changes.
            var summary = state.Available
                ? $"{(state.IsPlaying ? "playing" : "paused")} {state.Title} — {state.Artist}"
                : "not available";
            if (Interlocked.Exchange(ref _lastMediaSummary, summary) != summary)
                _log.LogInformation("Media: {State}", summary);
            Broadcast(Protocol.MediaState(state));
        };
        _audio.StateChanged += state =>
        {
            foreach (var client in _clients.Keys)
                SendAudio(client, state);
        };

        _ = Task.Run(RunCommandsAsync);
    }

    private async Task RunCommandsAsync()
    {
        await foreach (var (client, text, queued) in _commands.Reader.ReadAllAsync())
        {
            var waited = Stopwatch.GetElapsedTime(queued);
            if (waited > CommandMaxAge)
            {
                _log.LogWarning("Skipped a command from {Remote} that waited {Seconds:0} s: {Command}", client.Remote, waited.TotalSeconds, Summarize(text));
                continue;
            }

            var started = Stopwatch.GetTimestamp();
            using var slow = new System.Threading.Timer(_ => _log.LogWarning("Command from {Remote} still running after {Seconds:0} s: {Command}",
                client.Remote, SlowCommand.TotalSeconds, Summarize(text)), null, SlowCommand, Timeout.InfiniteTimeSpan);
            try
            {
                await HandleMessageAsync(client, text);
            }
            catch (Exception ex)
            {
                // E.g. COMException "The RPC server is unavailable" while the Windows audio service restarts.
                _log.LogError(ex, "Command from {Remote} failed: {Command}", client.Remote, Summarize(text));
            }
            var took = Stopwatch.GetElapsedTime(started);
            if (took > SlowCommand)
                _log.LogWarning("Command from {Remote} took {Seconds:0.0} s: {Command}", client.Remote, took.TotalSeconds, Summarize(text));
        }
    }

    private static string Summarize(string text) => text.Length <= 120 ? text : text[..120] + "…";

    public int ClientCount => _clients.Count;

    /// <summary>Remote addresses of the connected decks.</summary>
    public IReadOnlyList<string> ClientRemotes => _clients.Keys.Select(c => c.Remote).ToList();

    /// <summary>Raised on a thread-pool thread whenever a deck connects or disconnects.</summary>
    public event Action<IReadOnlyList<string>>? ClientsChanged;

    public async Task HandleAsync(WebSocket socket, string remote, CancellationToken ct)
    {
        var client = new DeckClient(socket, remote);
        _clients[client] = 0;
        _log.LogInformation("Deck connected: {Remote} ({Count} connected)", remote, ClientCount);
        ClientsChanged?.Invoke(ClientRemotes);

        var writer = client.RunWriterAsync(ct);
        try
        {
            client.Send(Protocol.Hello());
            if (_media.Art is { } art)
                client.Send(Protocol.MediaArt(art));
            client.Send(Protocol.MediaState(_media.Current));
            SendAudio(client, _audio.Current);
            client.Send(Protocol.DiscordState(_discord.Current));

            await ReceiveLoopAsync(client, ct);
        }
        catch (Exception ex) when (ex is WebSocketException or OperationCanceledException)
        {
            // Dropped connection; the deck reconnects on its own.
        }
        finally
        {
            _clients.TryRemove(client, out _);
            client.Complete();
            await writer;
            _log.LogInformation("Deck disconnected: {Remote} ({Count} connected)", remote, ClientCount);
            ClientsChanged?.Invoke(ClientRemotes);
        }
    }

    private void Broadcast(string message)
    {
        foreach (var client in _clients.Keys)
            client.Send(message);
    }

    /// <summary>Sends any icons the client hasn't received yet, then the audio state that references them.</summary>
    private void SendAudio(DeckClient client, AudioSnapshot state)
    {
        lock (client.SentIcons)
        {
            foreach (var hash in state.Sessions.Select(s => s.IconHash).OfType<string>())
            {
                if (client.SentIcons.Contains(hash) || _audio.GetIcon(hash) is not { } icon)
                    continue;
                client.Send(Protocol.AudioIcon(icon));
                client.SentIcons.Add(hash);
            }
            client.Send(Protocol.AudioState(state));
        }
    }

    private async Task ReceiveLoopAsync(DeckClient client, CancellationToken ct)
    {
        var buffer = new byte[4096];
        using var message = new MemoryStream();

        while (client.Socket.State == WebSocketState.Open)
        {
            var result = await client.Socket.ReceiveAsync(buffer, ct);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                await client.Socket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, null, ct);
                return;
            }

            message.Write(buffer, 0, result.Count);
            if (message.Length > MaxIncomingMessageBytes)
            {
                await client.Socket.CloseOutputAsync(WebSocketCloseStatus.MessageTooBig, null, ct);
                return;
            }
            if (!result.EndOfMessage)
                continue;

            if (result.MessageType == WebSocketMessageType.Text)
            {
                var text = Encoding.UTF8.GetString(message.GetBuffer(), 0, (int)message.Length);
                if (text.Contains("\"ping\"") && IsPing(text))
                    client.Send(Protocol.Pong());  // answered right here, so it works even while a command hangs
                else
                    _commands.Writer.TryWrite((client, text, Stopwatch.GetTimestamp()));
            }
            message.SetLength(0);
        }
    }

    private static bool IsPing(string text)
    {
        try
        {
            using var doc = JsonDocument.Parse(text);
            return doc.RootElement.TryGetProperty("type", out var type) && type.GetString() == "ping";
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private async Task HandleMessageAsync(DeckClient client, string text)
    {
        try
        {
            using var doc = JsonDocument.Parse(text);
            var root = doc.RootElement;
            var type = root.GetProperty("type").GetString();

            switch (type)
            {
                case "hello":
                    _log.LogInformation("Deck {Remote} is {Client} (protocol {Protocol})", client.Remote,
                        root.TryGetProperty("client", out var name) ? name.GetString() : "?",
                        root.TryGetProperty("protocol", out var version) ? version.GetInt32() : 0);
                    break;
                case "ping":
                    client.Send(Protocol.Pong());
                    break;
                case "media.command":
                    var action = root.GetProperty("action").GetString() ?? "";
                    if (!await _media.SendCommandAsync(action))
                        _log.LogWarning("Media command '{Action}' failed", action);
                    break;
                case "audio.set_volume":
                    _audio.SetVolume(root.GetProperty("target").GetString() ?? "", (float)root.GetProperty("volume").GetDouble());
                    break;
                case "audio.set_mute":
                    _audio.SetMute(root.GetProperty("target").GetString() ?? "", root.GetProperty("muted").GetBoolean());
                    break;
                case "pc.power":
                    // The deck asks for confirmation before sending this; the 5 s delay shows Windows' own
                    // shutdown notice and leaves room for `shutdown /a`.
                    if (root.GetProperty("action").GetString() == "shutdown")
                    {
                        _log.LogWarning("Shutdown requested by deck {Remote}", client.Remote);
                        Process.Start(new ProcessStartInfo("shutdown.exe", "/s /t 5 /c \"Shut down from the Pi Media Deck\"")
                        {
                            UseShellExecute = false,
                            CreateNoWindow = true,
                        });
                    }
                    break;
                case "discord.command":
                    var discordAction = root.GetProperty("action").GetString();
                    try
                    {
                        await (discordAction switch
                        {
                            "toggle_mute" => _discord.ToggleMuteAsync(),
                            "toggle_deafen" => _discord.ToggleDeafenAsync(),
                            _ => Task.CompletedTask,
                        });
                    }
                    catch (Exception ex) when (ex is RpcException or IOException or TimeoutException)
                    {
                        _log.LogWarning("Discord command '{Action}' failed: {Error}", discordAction, ex.Message);
                    }
                    break;
                case "audio.set_output":
                    var outputId = root.GetProperty("id").GetString() ?? "";
                    if (_audio.SetOutput(outputId))
                        _log.LogInformation("Output switched to {Output}", _audio.Current.Outputs.FirstOrDefault(o => o.Id == outputId)?.Name);
                    else
                        _log.LogWarning("Unknown output '{Id}'", outputId);
                    break;
                default:
                    _log.LogWarning("Unknown message type '{Type}' from {Remote}", type, client.Remote);
                    break;
            }
        }
        catch (Exception ex) when (ex is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        {
            _log.LogWarning("Malformed message from {Remote}: {Error}", client.Remote, ex.Message);
        }
    }

    /// <summary>One connected deck. Outgoing messages go through a queue so only one send runs at a time.</summary>
    private sealed class DeckClient(WebSocket socket, string remote)
    {
        private readonly Channel<string> _outbox = Channel.CreateUnbounded<string>(new UnboundedChannelOptions { SingleReader = true });

        public WebSocket Socket { get; } = socket;
        public string Remote { get; } = remote;
        public HashSet<string> SentIcons { get; } = [];

        public void Send(string message) => _outbox.Writer.TryWrite(message);

        public void Complete() => _outbox.Writer.TryComplete();

        public async Task RunWriterAsync(CancellationToken ct)
        {
            try
            {
                await foreach (var message in _outbox.Reader.ReadAllAsync(ct))
                    await Socket.SendAsync(Encoding.UTF8.GetBytes(message), WebSocketMessageType.Text, true, ct);
            }
            catch (Exception ex) when (ex is WebSocketException or OperationCanceledException)
            {
                // The receive loop sees the same failure and cleans up.
            }
        }
    }
}
