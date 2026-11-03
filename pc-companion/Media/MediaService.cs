using System.Security.Cryptography;
using Windows.Foundation;
using Windows.Graphics.Imaging;
using Windows.Media.Control;
using Windows.Storage.Streams;

namespace PcCompanion.Media;

/// <summary>Now-playing state, shaped like the protocol's <c>media.state</c> message.</summary>
public sealed record MediaSnapshot(
    bool Available,
    string? App,
    string Title,
    string Artist,
    string Album,
    long DurationMs,
    long PositionMs,
    long PositionUpdatedAt,
    bool IsPlaying,
    bool CanPrevious,
    bool CanNext,
    string? ArtHash)
{
    public static MediaSnapshot Unavailable { get; } =
        new(false, null, "", "", "", 0, 0, 0, false, false, false, null);
}

public sealed record AlbumArt(string Hash, string Mime, byte[] Data);

/// <summary>Tracks the Spotify session in the Windows media transport controls (SMTC).</summary>
public sealed class MediaService : IDisposable
{
    private const int ArtSize = 300;
    private const float ArtQuality = 0.85f;

    private readonly GlobalSystemMediaTransportControlsSessionManager _manager;
    private readonly SemaphoreSlim _refreshGate = new(1, 1);
    private GlobalSystemMediaTransportControlsSession? _session;
    private string? _artTrackKey;

    public event Action<MediaSnapshot>? StateChanged;
    public event Action<AlbumArt>? ArtChanged;

    public MediaSnapshot Current { get; private set; } = MediaSnapshot.Unavailable;
    public AlbumArt? Art { get; private set; }

    private MediaService(GlobalSystemMediaTransportControlsSessionManager manager)
    {
        _manager = manager;
        _manager.SessionsChanged += OnSessionsChanged;
    }

    public static async Task<MediaService> CreateAsync()
    {
        var manager = await GlobalSystemMediaTransportControlsSessionManager.RequestAsync();
        var service = new MediaService(manager);
        service.SelectSession();
        await service.RefreshAsync();
        return service;
    }

    /// <summary>Runs a protocol media action: <c>play_pause</c>, <c>next</c> or <c>previous</c>.</summary>
    public async Task<bool> SendCommandAsync(string action)
    {
        var session = _session;
        if (session is null)
            return false;

        return action switch
        {
            "play_pause" => await session.TryTogglePlayPauseAsync(),
            "next" => await session.TrySkipNextAsync(),
            "previous" => await session.TrySkipPreviousAsync(),
            _ => false,
        };
    }

    private void SelectSession()
    {
        var spotify = _manager.GetSessions().FirstOrDefault(s =>
            s.SourceAppUserModelId.Contains("Spotify", StringComparison.OrdinalIgnoreCase));

        if (spotify?.SourceAppUserModelId == _session?.SourceAppUserModelId)
            return;

        if (_session is not null)
        {
            _session.MediaPropertiesChanged -= OnMediaPropertiesChanged;
            _session.PlaybackInfoChanged -= OnPlaybackInfoChanged;
            _session.TimelinePropertiesChanged -= OnTimelinePropertiesChanged;
        }

        _session = spotify;
        _artTrackKey = null;

        if (_session is not null)
        {
            _session.MediaPropertiesChanged += OnMediaPropertiesChanged;
            _session.PlaybackInfoChanged += OnPlaybackInfoChanged;
            _session.TimelinePropertiesChanged += OnTimelinePropertiesChanged;
        }
    }

    private void OnSessionsChanged(GlobalSystemMediaTransportControlsSessionManager sender, SessionsChangedEventArgs args)
    {
        SelectSession();
        _ = RefreshAsync();
    }

    private void OnMediaPropertiesChanged(GlobalSystemMediaTransportControlsSession sender, MediaPropertiesChangedEventArgs args) =>
        _ = RefreshAsync();

    private void OnPlaybackInfoChanged(GlobalSystemMediaTransportControlsSession sender, PlaybackInfoChangedEventArgs args) =>
        _ = RefreshAsync();

    private void OnTimelinePropertiesChanged(GlobalSystemMediaTransportControlsSession sender, TimelinePropertiesChangedEventArgs args) =>
        _ = RefreshAsync();

    private async Task RefreshAsync()
    {
        await _refreshGate.WaitAsync();
        try
        {
            var snapshot = await ReadSnapshotAsync();

            // While paused Spotify keeps re-publishing the same position with a new timestamp; that's not a change.
            if (Current.Available && !snapshot.IsPlaying && !Current.IsPlaying && snapshot.PositionMs == Current.PositionMs)
                snapshot = snapshot with { PositionUpdatedAt = Current.PositionUpdatedAt };

            if (snapshot != Current)
            {
                Current = snapshot;
                StateChanged?.Invoke(snapshot);
            }
        }
        catch (Exception ex)
        {
            // The session can vanish mid-read when Spotify closes; the next SessionsChanged recovers.
            Console.Error.WriteLine($"[media] refresh failed: {ex.Message}");
        }
        finally
        {
            _refreshGate.Release();
        }
    }

    private async Task<MediaSnapshot> ReadSnapshotAsync()
    {
        var session = _session;
        if (session is null)
            return MediaSnapshot.Unavailable;

        var props = await session.TryGetMediaPropertiesAsync();
        var playback = session.GetPlaybackInfo();
        var timeline = session.GetTimelineProperties();
        var artHash = await UpdateArtAsync(props);

        var updatedAt = timeline.LastUpdatedTime.Year < 2000 ? DateTimeOffset.Now : timeline.LastUpdatedTime;

        return new MediaSnapshot(
            Available: true,
            App: "Spotify",
            Title: props.Title ?? "",
            Artist: props.Artist ?? "",
            Album: props.AlbumTitle ?? "",
            DurationMs: (long)(timeline.EndTime - timeline.StartTime).TotalMilliseconds,
            PositionMs: (long)timeline.Position.TotalMilliseconds,
            PositionUpdatedAt: updatedAt.ToUnixTimeMilliseconds(),
            IsPlaying: playback.PlaybackStatus == GlobalSystemMediaTransportControlsSessionPlaybackStatus.Playing,
            CanPrevious: playback.Controls.IsPreviousEnabled,
            CanNext: playback.Controls.IsNextEnabled,
            ArtHash: artHash);
    }

    /// <summary>Loads art once per track. Spotify often publishes the thumbnail after the title, so a missing one is retried.</summary>
    private async Task<string?> UpdateArtAsync(GlobalSystemMediaTransportControlsSessionMediaProperties props)
    {
        var trackKey = $"{props.Title}\n{props.Artist}\n{props.AlbumTitle}";
        if (trackKey == _artTrackKey)
            return Art?.Hash;

        if (props.Thumbnail is null)
            return null;

        var art = await LoadArtAsync(props.Thumbnail);
        _artTrackKey = trackKey;
        if (art.Hash != Art?.Hash)
        {
            Art = art;
            ArtChanged?.Invoke(art);
        }
        return art.Hash;
    }

    private static async Task<AlbumArt> LoadArtAsync(IRandomAccessStreamReference thumbnail)
    {
        using var input = await thumbnail.OpenReadAsync();
        var decoder = await BitmapDecoder.CreateAsync(input);

        var scale = Math.Min(1.0, (double)ArtSize / Math.Max(decoder.PixelWidth, decoder.PixelHeight));
        var transform = new BitmapTransform
        {
            ScaledWidth = (uint)Math.Max(1, Math.Round(decoder.PixelWidth * scale)),
            ScaledHeight = (uint)Math.Max(1, Math.Round(decoder.PixelHeight * scale)),
            InterpolationMode = BitmapInterpolationMode.Fant,
        };
        var pixels = await decoder.GetPixelDataAsync(
            BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore, transform,
            ExifOrientationMode.RespectExifOrientation, ColorManagementMode.DoNotColorManage);

        using var output = new InMemoryRandomAccessStream();
        var options = new BitmapPropertySet
        {
            ["ImageQuality"] = new BitmapTypedValue(ArtQuality, PropertyType.Single),
        };
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.JpegEncoderId, output, options);
        encoder.SetPixelData(
            BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore,
            transform.ScaledWidth, transform.ScaledHeight,
            decoder.DpiX, decoder.DpiY, pixels.DetachPixelData());
        await encoder.FlushAsync();

        var data = new byte[output.Size];
        using (var reader = new DataReader(output.GetInputStreamAt(0)))
        {
            await reader.LoadAsync((uint)output.Size);
            reader.ReadBytes(data);
        }

        var hash = Convert.ToHexString(SHA1.HashData(data))[..16].ToLowerInvariant();
        return new AlbumArt(hash, "image/jpeg", data);
    }

    public void Dispose()
    {
        _manager.SessionsChanged -= OnSessionsChanged;
        if (_session is not null)
        {
            _session.MediaPropertiesChanged -= OnMediaPropertiesChanged;
            _session.PlaybackInfoChanged -= OnPlaybackInfoChanged;
            _session.TimelinePropertiesChanged -= OnTimelinePropertiesChanged;
        }
        _refreshGate.Dispose();
    }
}
