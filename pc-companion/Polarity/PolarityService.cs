using System.Collections.Concurrent;
using System.Diagnostics;
using Microsoft.Extensions.Logging;
using PcCompanion.Media;

namespace PcCompanion.Polarity;

public sealed record PolarityNowPlaying(
    string Title,
    string? Subtitle,
    string? PosterHash,
    long DurationMs,
    long PositionMs,
    long PositionUpdatedAt,
    bool IsPlaying);

public sealed record PolarityCard(string Id, string Title, string? Subtitle, string? PosterHash, double? Progress);

/// <summary>Shaped like the protocol's <c>polarity.state</c>.</summary>
public sealed record PolarityState(
    bool Running,
    PolarityNowPlaying? Playing,
    IReadOnlyList<PolarityCard> Continue,
    IReadOnlyList<PolarityCard> Library)
{
    public static PolarityState NotRunning { get; } = new(false, null, [], []);

    /// <summary>Equal apart from the playhead moving on as expected (no need to resend for that).</summary>
    public bool SameAs(PolarityState other)
    {
        if (Running != other.Running || !Continue.SequenceEqual(other.Continue) || !Library.SequenceEqual(other.Library))
            return false;
        if (Playing is null || other.Playing is null)
            return Playing == other.Playing;
        var a = Playing;
        var b = other.Playing;
        if (a with { PositionMs = 0, PositionUpdatedAt = 0 } != b with { PositionMs = 0, PositionUpdatedAt = 0 })
            return false;
        // Same playhead if b's position is where a's would have got to by then (2 s slack for jitter).
        var expected = a.IsPlaying ? a.PositionMs + (b.PositionUpdatedAt - a.PositionUpdatedAt) : a.PositionMs;
        return Math.Abs(expected - b.PositionMs) < 2000;
    }
}

/// <summary>
/// Polarity (a separate app; nothing in it is changed) for the deck: running or not, what its mpv player is playing,
/// and Continue Watching + library from its data file. Posters are fetched here and sent as images, never as links.
/// </summary>
public sealed class PolarityService : IDisposable
{
    private static readonly string[] ProcessNames = ["Polarity", "polar"];
    private static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(2);
    private const int PosterWidth = 200;
    private const int PosterHeight = 300;
    private const int MaxPosters = 80;

    private readonly ILogger<PolarityService> _log;
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(10) };
    private readonly CancellationTokenSource _stop = new();
    private readonly ConcurrentDictionary<string, AlbumArt> _postersByHash = new();
    private readonly ConcurrentDictionary<string, string?> _hashByUrl = new();
    private readonly SemaphoreSlim _fetchGate = new(2, 2);
    private readonly object _sync = new();
    private FileSystemWatcher? _watcher;
    private PolarityData _data = PolarityData.Empty;
    private MpvClient? _mpv;
    private int? _pid;
    private Task? _loop;
    private int _reloadQueued;

    public event Action<PolarityState>? StateChanged;
    /// <summary>A poster became available; it's sent to decks before any state that references it.</summary>
    public event Action<AlbumArt>? PosterReady;

    public PolarityState Current { get; private set; } = PolarityState.NotRunning;

    public PolarityService(ILogger<PolarityService> log)
    {
        _log = log;
        _http.DefaultRequestHeaders.UserAgent.ParseAdd("pi-media-deck/0.1");
    }

    public AlbumArt? GetPoster(string hash) => _postersByHash.GetValueOrDefault(hash);

    public void Start() => _loop ??= RunAsync(_stop.Token);

    /// <summary>
    /// Protocol <c>polarity.command</c>: only play_pause. False if nothing is playing. (Seeking from outside doesn't
    /// stick: Polarity puts mpv back where its own player is within ~0.3 s, so there are no skip buttons.)
    /// </summary>
    public async Task<bool> SendCommandAsync(string action)
    {
        var mpv = _mpv;
        if (mpv is null || mpv.Current.Idle || action != "play_pause")
            return false;
        await mpv.TogglePauseAsync();
        return true;
    }

    private async Task RunAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(PollInterval);
        do
        {
            try
            {
                await PollAsync(ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.LogWarning("Polarity poll failed: {Error}", ex.Message);
            }
        }
        while (await timer.WaitForNextTickAsync(ct));
    }

    private async Task PollAsync(CancellationToken ct)
    {
        var pid = FindPolarity();
        if (pid != _pid)
        {
            _pid = pid;
            await DropMpvAsync();
            if (pid is null)
            {
                StopWatching();
                _log.LogInformation("Polarity: not running");
            }
            else
            {
                _log.LogInformation("Polarity: running");
                StartWatching();
                await ReloadAsync();
            }
            Publish();
        }
        if (pid is { } p && _mpv is null && await MpvClient.TryConnectAsync(p, ct) is { } mpv)
        {
            _log.LogInformation("Polarity: connected to its player");
            _mpv = mpv;
            mpv.StateChanged += _ => Publish();
            _ = Task.Run(async () =>
            {
                try
                {
                    await mpv.RunAsync(ct);
                }
                catch (Exception ex) when (ex is IOException or ObjectDisposedException or OperationCanceledException)
                {
                }
                if (ReferenceEquals(_mpv, mpv))
                {
                    _log.LogInformation("Polarity: its player closed");
                    await DropMpvAsync();
                    Publish();
                }
            }, ct);
        }
    }

    private static int? FindPolarity()
    {
        foreach (var name in ProcessNames)
        {
            var processes = Process.GetProcessesByName(name);
            try
            {
                if (processes.Length > 0)
                    return processes.Min(p => p.Id);
            }
            finally
            {
                foreach (var process in processes)
                    process.Dispose();
            }
        }
        return null;
    }

    private void StartWatching()
    {
        StopWatching();
        var folder = Path.GetDirectoryName(PolarityLibrary.DataFile)!;
        if (!Directory.Exists(folder))
            return;
        // Polarity saves progress every 5 s while playing; reload (debounced) whenever the file changes.
        _watcher = new FileSystemWatcher(folder, Path.GetFileName(PolarityLibrary.DataFile))
        {
            NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size,
        };
        _watcher.Changed += (_, _) => QueueReload();
        _watcher.Created += (_, _) => QueueReload();
        _watcher.Renamed += (_, _) => QueueReload();
        _watcher.EnableRaisingEvents = true;
    }

    private void StopWatching()
    {
        _watcher?.Dispose();
        _watcher = null;
        lock (_sync)
            _data = PolarityData.Empty;
    }

    private void QueueReload()
    {
        if (Interlocked.Exchange(ref _reloadQueued, 1) == 1)
            return;
        _ = Task.Run(async () =>
        {
            await Task.Delay(300);
            Interlocked.Exchange(ref _reloadQueued, 0);
            await ReloadAsync();
            Publish();
        });
    }

    private async Task ReloadAsync()
    {
        for (var attempt = 0; attempt < 3; attempt++)
        {
            try
            {
                var data = PolarityLibrary.Read(PolarityLibrary.DataFile);
                lock (_sync)
                    _data = data;
                break;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or System.Text.Json.JsonException)
            {
                await Task.Delay(200); // mid-write: try again
            }
        }
        // Fetch posters for everything shown; each one triggers a publish when it arrives.
        PolarityData snapshot;
        lock (_sync)
            snapshot = _data;
        var urls = PolarityLibrary.ContinueWatching(snapshot).Select(p => p.PosterUrl)
            .Concat(PolarityLibrary.RecentLibrary(snapshot).Select(t => t.PosterUrl))
            .Concat(snapshot.Progress.OrderByDescending(p => p.UpdatedAt).Take(1).Select(p => p.PosterUrl));
        foreach (var url in urls.OfType<string>().Distinct())
            _ = FetchPosterAsync(url);
    }

    private async Task FetchPosterAsync(string url)
    {
        if (_hashByUrl.ContainsKey(url) || _hashByUrl.Count >= MaxPosters)
            return;
        _hashByUrl[url] = null; // in progress (or failed): don't fetch again
        await _fetchGate.WaitAsync();
        try
        {
            var bytes = await _http.GetByteArrayAsync(url, _stop.Token);
            var poster = await ArtEncoder.EncodeAsync(bytes, PosterWidth, PosterHeight);
            _postersByHash[poster.Hash] = poster;
            _hashByUrl[url] = poster.Hash;
            PosterReady?.Invoke(poster);
            Publish();
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or IOException or System.Runtime.InteropServices.COMException or ArgumentException)
        {
            _log.LogInformation("Polarity: a poster couldn't be loaded ({Error})", ex.GetType().Name); // never the link
        }
        finally
        {
            _fetchGate.Release();
        }
    }

    private string? PosterHash(string? url) => url is null ? null : _hashByUrl.GetValueOrDefault(url);

    private void Publish()
    {
        PolarityState state;
        lock (_sync)
        {
            state = Build();
            if (state.SameAs(Current))
                return;
            Current = state;
        }
        StateChanged?.Invoke(state);
    }

    private PolarityState Build()
    {
        if (_pid is null)
            return PolarityState.NotRunning;

        var continueCards = PolarityLibrary.ContinueWatching(_data)
            .Select(p => new PolarityCard(p.MetaId, p.Title, p.Subtitle, PosterHash(p.PosterUrl), Math.Round(p.Fraction, 3)))
            .ToList();
        var libraryCards = PolarityLibrary.RecentLibrary(_data)
            .Select(t => new PolarityCard(t.Id, t.Title, null, PosterHash(t.PosterUrl), null))
            .ToList();
        return new PolarityState(true, NowPlaying(), continueCards, libraryCards);
    }

    private PolarityNowPlaying? NowPlaying()
    {
        var mpv = _mpv?.Current;
        if (mpv is null || mpv.Idle || mpv.DurationS is not > 0)
            return null;

        // Title and poster: the progress entry Polarity saves while playing (every 5 s), matched on length.
        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var entry = _data.Progress
            .Where(p => now - p.UpdatedAt < TimeSpan.FromHours(6).TotalMilliseconds)
            .Where(p => p.DurationS <= 0 || Math.Abs(p.DurationS - mpv.DurationS.Value) < 5)
            .MaxBy(p => p.UpdatedAt);
        return new PolarityNowPlaying(
            Title: entry?.Title ?? mpv.MediaTitle ?? "Playing",
            Subtitle: entry?.Subtitle,
            PosterHash: PosterHash(entry?.PosterUrl),
            DurationMs: (long)(mpv.DurationS.Value * 1000),
            PositionMs: (long)((mpv.PositionS ?? 0) * 1000),
            PositionUpdatedAt: now,
            IsPlaying: !mpv.Paused);
    }

    private async Task DropMpvAsync()
    {
        var mpv = Interlocked.Exchange(ref _mpv, null);
        if (mpv is not null)
            await mpv.DisposeAsync();
    }

    public void Dispose()
    {
        _stop.Cancel();
        StopWatching();
        DropMpvAsync().GetAwaiter().GetResult();
        _http.Dispose();
        _fetchGate.Dispose();
        _stop.Dispose();
    }
}
