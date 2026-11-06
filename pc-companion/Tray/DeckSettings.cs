using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace PcCompanion.Tray;

/// <summary>What the deck shows (the Volume tab always). Sent to decks as <c>deck.settings</c>.</summary>
public sealed record DeckSettings(
    bool Spotify = true,
    bool Polarity = true,
    bool Lights = true,
    bool Discord = true,
    bool Power = true,
    bool Outputs = true);

/// <summary>Loads and saves <see cref="DeckSettings"/> in the app's data folder (<c>deck-settings.json</c>).</summary>
public sealed class DeckSettingsStore
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web) { WriteIndented = true };

    private readonly string _path;
    private readonly ILogger<DeckSettingsStore> _log;
    private readonly object _sync = new();

    public event Action<DeckSettings>? Changed;

    public DeckSettings Current { get; private set; }

    public DeckSettingsStore(string dataDirectory, ILogger<DeckSettingsStore> log)
    {
        _path = Path.Combine(dataDirectory, "deck-settings.json");
        _log = log;
        Current = Load();
    }

    public void Update(DeckSettings settings)
    {
        lock (_sync)
        {
            if (settings == Current)
                return;
            Current = settings;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
                File.WriteAllText(_path, JsonSerializer.Serialize(settings, Json));
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                _log.LogWarning("Couldn't save the deck settings: {Error}", ex.Message);
            }
        }
        _log.LogInformation("Deck settings: {Settings}", settings);
        Changed?.Invoke(settings);
    }

    private DeckSettings Load()
    {
        try
        {
            if (File.Exists(_path))
                return JsonSerializer.Deserialize<DeckSettings>(File.ReadAllText(_path), Json) ?? new DeckSettings();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            _log.LogWarning("Couldn't read the deck settings, using defaults: {Error}", ex.Message);
        }
        return new DeckSettings();
    }
}
