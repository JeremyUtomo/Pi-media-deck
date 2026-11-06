using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace PcCompanion.Polarity;

/// <summary>What Polarity's mpv is doing, from the properties we observe.</summary>
public sealed record MpvState(bool Idle, bool Paused, double? PositionS, double? DurationS, string? MediaTitle)
{
    public static MpvState Disconnected { get; } = new(true, true, null, null, null);
}

/// <summary>
/// A second client on Polarity's mpv JSON IPC pipe (<c>\\.\pipe\polar-mpv-&lt;pid&gt;</c>; mpv serves each client
/// on its own pipe instance). Observes pause, time-pos, duration, idle-active and media-title, and sends only
/// play/pause — Polarity observes the same properties, so its own player bar follows. (Seeks from outside are
/// undone by Polarity within ~0.3 s, so none are sent.)
/// </summary>
public sealed class MpvClient : IAsyncDisposable
{
    private static readonly string[] Observed = ["pause", "time-pos", "duration", "idle-active", "media-title"];

    private readonly NamedPipeClientStream _pipe;
    private readonly SemaphoreSlim _writeGate = new(1, 1);
    private readonly Dictionary<string, JsonNode?> _properties = [];
    private readonly object _sync = new();

    public event Action<MpvState>? StateChanged;

    public MpvState Current { get; private set; } = MpvState.Disconnected;

    private MpvClient(NamedPipeClientStream pipe) => _pipe = pipe;

    public static string PipeName(int polarityPid) => $"polar-mpv-{polarityPid}";

    /// <summary>Connects if Polarity's player is up (it starts on first playback). Null if there's no pipe.</summary>
    public static async Task<MpvClient?> TryConnectAsync(int polarityPid, CancellationToken ct)
    {
        var name = PipeName(polarityPid);
        if (!File.Exists($@"\\.\pipe\{name}"))
            return null;
        var pipe = new NamedPipeClientStream(".", name, PipeDirection.InOut, PipeOptions.Asynchronous);
        try
        {
            await pipe.ConnectAsync(1000, ct);
        }
        catch (Exception ex) when (ex is TimeoutException or IOException or UnauthorizedAccessException)
        {
            await pipe.DisposeAsync();
            return null;
        }
        var client = new MpvClient(pipe);
        for (var i = 0; i < Observed.Length; i++)
            await client.SendAsync(new JsonArray("observe_property", 1000 + i, Observed[i]));
        return client;
    }

    public Task TogglePauseAsync() => SendAsync(new JsonArray("cycle", "pause"));

    /// <summary>Reads events until the pipe closes (mpv quit or Polarity shut it down after idling).</summary>
    public async Task RunAsync(CancellationToken ct)
    {
        using var reader = new StreamReader(_pipe, Encoding.UTF8, leaveOpen: true);
        while (!ct.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(ct);
            if (line is null)
                break;
            OnLine(line);
        }
    }

    private void OnLine(string line)
    {
        JsonNode? message;
        try
        {
            message = JsonNode.Parse(line);
        }
        catch (JsonException)
        {
            return;
        }
        if (message?["event"]?.GetValue<string>() != "property-change" || message["name"]?.GetValue<string>() is not { } name)
            return;

        MpvState state;
        lock (_sync)
        {
            _properties[name] = message["data"]?.DeepClone();
            state = new MpvState(
                Idle: Bool("idle-active") ?? true,
                Paused: Bool("pause") ?? false,
                PositionS: Double("time-pos"),
                DurationS: Double("duration"),
                MediaTitle: _properties.GetValueOrDefault("media-title") is JsonValue t && t.TryGetValue(out string? s) ? s : null);
            if (state == Current)
                return;
            Current = state;
        }
        StateChanged?.Invoke(state);
    }

    private bool? Bool(string name) =>
        _properties.GetValueOrDefault(name) is JsonValue v && v.TryGetValue(out bool b) ? b : null;

    private double? Double(string name) =>
        _properties.GetValueOrDefault(name) is JsonValue v && v.TryGetValue(out double d) ? d : null;

    private async Task SendAsync(JsonArray command)
    {
        var bytes = Encoding.UTF8.GetBytes(new JsonObject { ["command"] = command }.ToJsonString() + "\n");
        await _writeGate.WaitAsync();
        try
        {
            await _pipe.WriteAsync(bytes);
            await _pipe.FlushAsync();
        }
        finally
        {
            _writeGate.Release();
        }
    }

    public async ValueTask DisposeAsync()
    {
        await _pipe.DisposeAsync();
        _writeGate.Dispose();
    }
}
