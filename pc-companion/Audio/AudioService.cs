using System.Collections.Concurrent;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using NAudio.CoreAudioApi;
using NAudio.CoreAudioApi.Interfaces;

namespace PcCompanion.Audio;

/// <summary>One mixer strip: all audio sessions of a process, grouped. Shaped like a protocol <c>audio.state</c> session.</summary>
public sealed record AudioSessionInfo(string Id, string Name, float Volume, bool Muted, string? IconHash);

/// <summary>An active playback device (Core Audio render endpoint).</summary>
public sealed record AudioOutput(string Id, string Name);

public sealed record AudioSnapshot(
    float MasterVolume,
    bool MasterMuted,
    IReadOnlyList<AudioSessionInfo> Sessions,
    string OutputId,
    IReadOnlyList<AudioOutput> Outputs)
{
    public bool Matches(AudioSnapshot other) =>
        MasterVolume == other.MasterVolume && MasterMuted == other.MasterMuted && OutputId == other.OutputId
        && Sessions.SequenceEqual(other.Sessions) && Outputs.SequenceEqual(other.Outputs);
}

public sealed record AudioIcon(string Hash, string Mime, byte[] Data);

/// <summary>
/// Reads and sets master and per-app volume on the default render device (Core Audio via NAudio).
/// Polls for changes made elsewhere (other apps, the Windows mixer, new/closed sessions) and raises <see cref="StateChanged"/>.
/// </summary>
/// <remarks>
/// A Core Audio call can occasionally hang for good (seen with the Focusrite driver: the poll stuck inside
/// <c>ReadSnapshot</c> while Windows itself answered fresh queries instantly, so the output on the deck stopped
/// updating). Everything those calls touch lives in a <see cref="Core"/>. A poll that doesn't finish within
/// <see cref="StallLimit"/> is abandoned with its Core (its thread may never return) and polling starts over on a
/// fresh one. Commands wait at most <see cref="CommandLockWait"/> for the lock instead of queueing behind a hang.
/// </remarks>
public sealed class AudioService : IDisposable
{
    public const string MasterId = "master";

    private const int IconSize = 64;
    private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(250);
    /// <summary>Re-list sessions at least this often, even without a "session created" notification.</summary>
    private static readonly TimeSpan SessionRefreshInterval = TimeSpan.FromSeconds(5);
    private static readonly TimeSpan StallLimit = TimeSpan.FromSeconds(5);
    private static readonly TimeSpan CommandLockWait = TimeSpan.FromSeconds(2);
    /// <summary>At most one fresh start per this long, so a wedged audio service doesn't leak a thread per poll.</summary>
    private static readonly TimeSpan MinRestartInterval = TimeSpan.FromSeconds(30);

    private readonly ConcurrentDictionary<string, AudioIcon> _iconsByHash = new();
    private readonly CancellationTokenSource _stop = new();
    private readonly object _publishSync = new();
    private Core _core;
    private Task? _pollTask;
    private DateTime _lastRestart = DateTime.MinValue;

    public event Action<AudioSnapshot>? StateChanged;

    /// <summary>Warnings for the app log (stalls and fresh starts). Set before <see cref="Start"/>.</summary>
    public Action<string>? Log { get; set; }

    public AudioSnapshot Current { get; private set; }

    public AudioService()
    {
        _core = new Core(_iconsByHash);
        lock (_core.Sync)
            Current = _core.ReadSnapshot();
    }

    public void Start() => _pollTask ??= PollAsync(_stop.Token);

    public AudioIcon? GetIcon(string hash) => _iconsByHash.GetValueOrDefault(hash);

    /// <summary>Sets the volume (0.0–1.0) of <c>master</c> or every session of a process id. Returns false if nothing matched (or audio is stuck).</summary>
    public bool SetVolume(string target, float volume)
    {
        volume = Math.Clamp(volume, 0f, 1f);
        var matched = WithCore("set volume", core =>
        {
            var endpoint = core.CurrentEndpoint();
            if (target == MasterId)
            {
                endpoint.Volume.MasterVolumeLevelScalar = volume;
                return true;
            }
            var any = false;
            foreach (var session in core.ReadSessions(endpoint).Where(s => s.Identity.Id == target))
            {
                session.Control.SimpleAudioVolume.Volume = volume;
                any = true;
            }
            return any;
        });
        Refresh();
        return matched;
    }

    public bool SetMute(string target, bool muted)
    {
        var matched = WithCore("set mute", core =>
        {
            var endpoint = core.CurrentEndpoint();
            if (target == MasterId)
            {
                endpoint.Volume.Mute = muted;
                return true;
            }
            var any = false;
            foreach (var session in core.ReadSessions(endpoint).Where(s => s.Identity.Id == target))
            {
                session.Control.SimpleAudioVolume.Mute = muted;
                any = true;
            }
            return any;
        });
        Refresh();
        return matched;
    }

    /// <summary>Makes an active playback device the Windows default (all roles). Returns false for an unknown id.</summary>
    public bool SetOutput(string id)
    {
        if (Current.Outputs.All(o => o.Id != id))
            return false;
        // Not under the lock: it doesn't touch our device objects, and if the audio service stalls it shouldn't
        // also hold up the poll and volume commands.
        PolicyConfig.SetDefaultOutput(id);
        Refresh();
        return true;
    }

    /// <summary>Runs <paramref name="action"/> under the current Core's lock, or gives up after <see cref="CommandLockWait"/>.</summary>
    private bool WithCore(string what, Func<Core, bool> action)
    {
        var core = _core;
        if (!Monitor.TryEnter(core.Sync, CommandLockWait))
        {
            Log?.Invoke($"Audio: couldn't {what}: Core Audio is busy (stuck in '{core.Phase}')");
            return false;
        }
        try
        {
            return action(core);
        }
        finally
        {
            Monitor.Exit(core.Sync);
        }
    }

    private async Task PollAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(PollInterval);
        try
        {
            while (await timer.WaitForNextTickAsync(ct))
            {
                var core = _core;
                var refresh = Task.Run(Refresh, ct);
                if (await Task.WhenAny(refresh, Task.Delay(StallLimit, ct)) == refresh)
                {
                    if (refresh.Exception is { } failure)
                        Log?.Invoke($"Audio: poll failed: {failure.InnerException?.Message}"); // e.g. mid device switch; next tick retries
                    continue;
                }
                // Stuck in a Core Audio call. Abandon that Core (and its thread) and start over on fresh objects.
                Log?.Invoke($"Audio: poll stuck for {StallLimit.TotalSeconds:0} s in '{core.Phase}'");
                if (DateTime.UtcNow - _lastRestart < MinRestartInterval)
                    continue; // just started over: later polls find the lock taken and return; try again after a while
                _lastRestart = DateTime.UtcNow;
                try
                {
                    _core = await Task.Run(() => new Core(_iconsByHash), ct);
                    Log?.Invoke("Audio: started over with fresh Core Audio objects");
                }
                catch (Exception ex) when (ex is COMException or InvalidOperationException)
                {
                    Log?.Invoke($"Audio: couldn't start over: {ex.Message}");
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    /// <summary>Re-reads the state and raises <see cref="StateChanged"/> if it differs (in order, one at a time).</summary>
    private void Refresh()
    {
        var core = _core;
        if (!Monitor.TryEnter(core.Sync, CommandLockWait))
            return; // the poll (or a command) is busy or stuck there; the watchdog deals with it
        AudioSnapshot snapshot;
        try
        {
            snapshot = core.ReadSnapshot();
        }
        finally
        {
            Monitor.Exit(core.Sync);
        }
        lock (_publishSync)
        {
            if (!ReferenceEquals(core, _core) || snapshot.Matches(Current))
                return; // abandoned meanwhile, or nothing new
            Current = snapshot;
            StateChanged?.Invoke(snapshot);
        }
    }

    public void Dispose()
    {
        _stop.Cancel();
        try
        {
            _pollTask?.Wait(TimeSpan.FromSeconds(2));
        }
        catch (AggregateException)
        {
        }
        _stop.Dispose();
        if (Monitor.TryEnter(_core.Sync, TimeSpan.FromSeconds(1)))
        {
            _core.Dispose();
            Monitor.Exit(_core.Sync);
        }
    }

    /// <summary>Core Audio objects and the caches built from them, used only under <see cref="Sync"/>.</summary>
    private sealed class Core : IDisposable
    {
        private static readonly ProcessIdentity SystemSounds = new("system", "System sounds", null);

        private readonly MMDeviceEnumerator _enumerator = new();
        private readonly ConcurrentDictionary<string, AudioIcon> _iconsByHash;
        private readonly Dictionary<uint, ProcessIdentity> _identities = [];
        private readonly Dictionary<string, AudioIcon?> _iconsByPath = new(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, string> _outputNames = [];
        private Endpoint? _endpoint;

        public Core(ConcurrentDictionary<string, AudioIcon> iconsByHash) => _iconsByHash = iconsByHash;

        public object Sync { get; } = new();

        /// <summary>The step a poll is in, for the log if it hangs.</summary>
        public volatile string Phase = "idle";

        public AudioSnapshot ReadSnapshot()
        {
            var endpoint = CurrentEndpoint();
            Phase = "reading sessions";
            var raw = ReadSessions(endpoint).ToList();

            var livePids = raw.Select(s => s.Pid).ToHashSet();
            foreach (var pid in _identities.Keys.Where(pid => !livePids.Contains(pid)).ToList())
                _identities.Remove(pid);

            Phase = "reading session volumes";
            var sessions = raw
                .GroupBy(s => s.Identity.Id)
                .Select(g =>
                {
                    var first = g.First();
                    return new AudioSessionInfo(
                        first.Identity.Id,
                        first.Identity.Name,
                        first.Control.SimpleAudioVolume.Volume,
                        g.All(s => s.Control.SimpleAudioVolume.Mute),
                        first.Identity.IconHash);
                })
                .OrderBy(s => s.Name, StringComparer.OrdinalIgnoreCase)
                .ToList();

            Phase = "reading master volume";
            var master = endpoint.Volume.MasterVolumeLevelScalar;
            var muted = endpoint.Volume.Mute;
            Phase = "listing outputs";
            var outputs = ReadOutputs();
            Phase = "idle";
            return new AudioSnapshot(master, muted, sessions, endpoint.Id, outputs);
        }

        /// <summary>
        /// The default playback device with its volume control and session list, kept for as long as it stays the
        /// default. Rebuilding them every poll (4×/s) registered and unregistered Core Audio callbacks a dozen
        /// times a second; only reading which device is the default stays a per-poll call.
        /// </summary>
        public Endpoint CurrentEndpoint()
        {
            Phase = "getting the default output";
            var device = _enumerator.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);
            if (_endpoint is { } current && current.Id == device.ID)
            {
                device.Dispose();
                return current;
            }
            Phase = "opening the new default output";
            _endpoint?.Dispose();
            return _endpoint = new Endpoint(device);
        }

        /// <summary>Active playback devices, sorted by name. Names are cached: reading them opens each device's property store.</summary>
        private List<AudioOutput> ReadOutputs()
        {
            var outputs = new List<AudioOutput>();
            foreach (var device in _enumerator.EnumerateAudioEndPoints(DataFlow.Render, DeviceState.Active))
            {
                using (device)
                {
                    if (!_outputNames.TryGetValue(device.ID, out var name))
                        _outputNames[device.ID] = name = device.FriendlyName;
                    outputs.Add(new AudioOutput(device.ID, name));
                }
            }
            outputs.Sort((a, b) => StringComparer.OrdinalIgnoreCase.Compare(a.Name, b.Name));
            return outputs;
        }

        public IEnumerable<RawSession> ReadSessions(Endpoint endpoint)
        {
            // Re-list only when Windows reports a new session (or now and then as a safety net): RefreshSessions
            // re-registers the session notification each time. Closed sessions show up as Expired until then.
            if (endpoint.SessionsChanged || DateTime.UtcNow - endpoint.SessionsListedAt > SessionRefreshInterval)
            {
                endpoint.SessionsChanged = false;
                endpoint.SessionsListedAt = DateTime.UtcNow;
                Phase = "re-listing sessions";
                endpoint.Sessions.RefreshSessions();
            }
            var sessions = endpoint.Sessions.Sessions;

            for (var i = 0; i < sessions.Count; i++)
            {
                var control = sessions[i];
                if (control.State == AudioSessionState.AudioSessionStateExpired)
                    continue;

                var pid = control.GetProcessID;
                yield return new RawSession(pid, Identify(control, pid), control);
            }
        }

        /// <summary>Resolves id, display name and icon once per process; cached until the process stops playing.</summary>
        private ProcessIdentity Identify(AudioSessionControl control, uint pid)
        {
            if (control.IsSystemSoundsSession)
                return SystemSounds;
            if (_identities.TryGetValue(pid, out var cached))
                return cached;

            ProcessIdentity identity;
            try
            {
                using var process = Process.GetProcessById((int)pid);
                var id = process.ProcessName.ToLowerInvariant() + ".exe";

                string? exePath = null;
                try
                {
                    exePath = process.MainModule?.FileName;
                }
                catch (Exception ex) when (ex is Win32Exception or InvalidOperationException)
                {
                    // Elevated or protected process: fall back to the process name, no icon.
                }

                var name = exePath is null ? null : FileVersionInfo.GetVersionInfo(exePath).FileDescription;
                if (string.IsNullOrWhiteSpace(name))
                    name = string.IsNullOrWhiteSpace(control.DisplayName) || control.DisplayName.StartsWith('@')
                        ? process.ProcessName
                        : control.DisplayName;

                identity = new ProcessIdentity(id, name, exePath is null ? null : LoadIcon(exePath)?.Hash);
            }
            catch (ArgumentException)
            {
                // Process already exited.
                identity = new ProcessIdentity($"pid{pid}", $"Process {pid}", null);
            }

            _identities[pid] = identity;
            return identity;
        }

        private AudioIcon? LoadIcon(string exePath)
        {
            if (_iconsByPath.TryGetValue(exePath, out var cached))
                return cached;

            AudioIcon? icon = null;
            try
            {
                using var extracted = Icon.ExtractIcon(exePath, 0, IconSize);
                if (extracted is not null)
                {
                    using var bitmap = extracted.ToBitmap();
                    using var png = new MemoryStream();
                    bitmap.Save(png, ImageFormat.Png);
                    var data = png.ToArray();
                    var hash = Convert.ToHexString(SHA1.HashData(data))[..16].ToLowerInvariant();
                    icon = new AudioIcon(hash, "image/png", data);
                    _iconsByHash[hash] = icon;
                }
            }
            catch (Exception ex) when (ex is IOException or ArgumentException or ExternalException)
            {
                // No usable icon; the deck shows a generic one.
            }

            _iconsByPath[exePath] = icon;
            return icon;
        }

        public void Dispose()
        {
            _endpoint?.Dispose();
            _enumerator.Dispose();
        }
    }

    private sealed class Endpoint : IDisposable
    {
        public Endpoint(MMDevice device)
        {
            Device = device;
            Id = device.ID;
            // Both are created once and cached by NAudio on the device; each registers one Core Audio callback.
            Volume = device.AudioEndpointVolume;
            Sessions = device.AudioSessionManager;
            Sessions.OnSessionCreated += (_, _) => SessionsChanged = true;
            SessionsListedAt = DateTime.UtcNow;
        }

        public MMDevice Device { get; }
        public string Id { get; }
        public AudioEndpointVolume Volume { get; }
        public AudioSessionManager Sessions { get; }
        public volatile bool SessionsChanged;
        public DateTime SessionsListedAt;

        public void Dispose() => Device.Dispose(); // also releases (and unregisters) the volume and session manager
    }

    private sealed record ProcessIdentity(string Id, string Name, string? IconHash);

    private sealed record RawSession(uint Pid, ProcessIdentity Identity, AudioSessionControl Control);
}
