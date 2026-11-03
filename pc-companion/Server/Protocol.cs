using System.Text.Json;
using PcCompanion.Audio;
using PcCompanion.Discord;
using PcCompanion.Media;

namespace PcCompanion.Server;

/// <summary>Builds PC → Pi messages. See docs/CONTEXT.md §6.</summary>
public static class Protocol
{
    public const int Version = 1;
    public const string ServerName = "pc-companion/0.1";

    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    public static string Hello() => Serialize(new { type = "hello", protocol = Version, server = ServerName });

    public static string Pong() => Serialize(new { type = "pong" });

    public static string MediaState(MediaSnapshot s) => Serialize(new
    {
        type = "media.state",
        available = s.Available,
        app = s.App,
        title = s.Title,
        artist = s.Artist,
        album = s.Album,
        durationMs = s.DurationMs,
        positionMs = s.PositionMs,
        positionUpdatedAt = s.PositionUpdatedAt,
        isPlaying = s.IsPlaying,
        canPrevious = s.CanPrevious,
        canNext = s.CanNext,
        artHash = s.ArtHash,
    });

    public static string MediaArt(AlbumArt art) => Serialize(new
    {
        type = "media.art",
        hash = art.Hash,
        mime = art.Mime,
        data = Convert.ToBase64String(art.Data),
    });

    public static string AudioState(AudioSnapshot s) => Serialize(new
    {
        type = "audio.state",
        master = new { volume = s.MasterVolume, muted = s.MasterMuted },
        sessions = s.Sessions.Select(x => new { id = x.Id, name = x.Name, volume = x.Volume, muted = x.Muted, iconHash = x.IconHash }),
        output = s.OutputId,
        outputs = s.Outputs.Select(o => new { id = o.Id, name = o.Name }),
    });

    public static string DiscordState(DiscordState s) => Serialize(new
    {
        type = "discord.state",
        status = s.Status,
        inCall = s.InCall,
        channel = s.Channel,
        muted = s.Muted,
        deafened = s.Deafened,
    });

    public static string AudioIcon(AudioIcon icon) => Serialize(new
    {
        type = "audio.icon",
        hash = icon.Hash,
        mime = icon.Mime,
        data = Convert.ToBase64String(icon.Data),
    });

    private static string Serialize(object message) => JsonSerializer.Serialize(message, Json);
}
