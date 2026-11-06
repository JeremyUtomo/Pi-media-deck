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
public sealed class AudioService : IDisposable
{
    public const string MasterId = "master";

    private const int IconSize = 64;
    private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(250);

    private readonly object _sync = new();
    private readonly MMDeviceEnumerator _enumerator = new();
    private readonly Dictionary<uint, ProcessIdentity> _identities = [];
    private readonly Dictionary<string, AudioIcon?> _iconsByPath = new(StringComparer.OrdinalIgnoreCase);
    private readonly ConcurrentDictionary<string, AudioIcon> _iconsByHash = new();
    private readonly Dictionary<string, string> _outputNames = [];
    private readonly CancellationTokenSource _stop = new();
    private Task? _pollTask;

    public event Action<AudioSnapshot>? StateChanged;

    public AudioSnapshot Current { get; private set; }

    public AudioService()
    {
        lock (_sync)
            Current = ReadSnapshot();
    }

    public void Start() => _pollTask ??= PollAsync(_stop.Token);

    public AudioIcon? GetIcon(string hash) => _iconsByHash.GetValueOrDefault(hash);

    /// <summary>Sets the volume (0.0–1.0) of <c>master</c> or every session of a process id. Returns false if nothing matched.</summary>
    public bool SetVolume(string target, float volume)
    {
        volume = Math.Clamp(volume, 0f, 1f);
        bool matched;
        lock (_sync)
        {
            using var device = GetDevice();
            if (target == MasterId)
            {
                device.AudioEndpointVolume.MasterVolumeLevelScalar = volume;
                matched = true;
            }
            else
            {
                matched = false;
                foreach (var session in ReadSessions(device).Where(s => s.Identity.Id == target))
                {
                    session.Control.SimpleAudioVolume.Volume = volume;
                    matched = true;
                }
            }
        }
        Refresh();
        return matched;
    }

    public bool SetMute(string target, bool muted)
    {
        bool matched;
        lock (_sync)
        {
            using var device = GetDevice();
            if (target == MasterId)
            {
                device.AudioEndpointVolume.Mute = muted;
                matched = true;
            }
            else
            {
                matched = false;
                foreach (var session in ReadSessions(device).Where(s => s.Identity.Id == target))
                {
                    session.Control.SimpleAudioVolume.Mute = muted;
                    matched = true;
                }
            }
        }
        Refresh();
        return matched;
    }

    private async Task PollAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(PollInterval);
        try
        {
            while (await timer.WaitForNextTickAsync(ct))
            {
                try
                {
                    Refresh();
                }
                catch (Exception ex)
                {
                    // Device switches can make Core Audio calls fail briefly; the next tick retries.
                    Console.Error.WriteLine($"[audio] poll failed: {ex.Message}");
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    /// <summary>Re-reads the state and raises <see cref="StateChanged"/> if it differs. Raised under the lock so updates stay in order.</summary>
    private void Refresh()
    {
        lock (_sync)
        {
            var snapshot = ReadSnapshot();
            if (snapshot.Matches(Current))
                return;
            Current = snapshot;
            StateChanged?.Invoke(snapshot);
        }
    }

    private AudioSnapshot ReadSnapshot()
    {
        using var device = GetDevice();
        var endpoint = device.AudioEndpointVolume;
        var raw = ReadSessions(device).ToList();

        var livePids = raw.Select(s => s.Pid).ToHashSet();
        foreach (var pid in _identities.Keys.Where(pid => !livePids.Contains(pid)).ToList())
            _identities.Remove(pid);

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

        return new AudioSnapshot(endpoint.MasterVolumeLevelScalar, endpoint.Mute, sessions, device.ID, ReadOutputs());
    }

    private MMDevice GetDevice() => _enumerator.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);

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

    /// <summary>Makes an active playback device the Windows default (all roles). Returns false for an unknown id.</summary>
    public bool SetOutput(string id)
    {
        if (Current.Outputs.All(o => o.Id != id))
            return false;
        // Not under _sync: it doesn't touch our device objects, and if the audio service stalls it shouldn't
        // also hold up the poll and volume commands.
        PolicyConfig.SetDefaultOutput(id);
        Refresh();
        return true;
    }

    private sealed record ProcessIdentity(string Id, string Name, string? IconHash);

    private sealed record RawSession(uint Pid, ProcessIdentity Identity, AudioSessionControl Control);

    private IEnumerable<RawSession> ReadSessions(MMDevice device)
    {
        var manager = device.AudioSessionManager;
        manager.RefreshSessions();
        var sessions = manager.Sessions;

        for (var i = 0; i < sessions.Count; i++)
        {
            var control = sessions[i];
            if (control.State == AudioSessionState.AudioSessionStateExpired)
                continue;

            var pid = control.GetProcessID;
            yield return new RawSession(pid, Identify(control, pid), control);
        }
    }

    private static readonly ProcessIdentity SystemSounds = new("system", "System sounds", null);

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
        _stop.Cancel();
        try
        {
            _pollTask?.Wait();
        }
        catch (AggregateException)
        {
        }
        _stop.Dispose();
        _enumerator.Dispose();
    }
}
