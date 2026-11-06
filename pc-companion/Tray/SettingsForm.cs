namespace PcCompanion.Tray;

/// <summary>"Deck settings": what the deck shows. Changes apply right away (there's nothing to confirm).</summary>
internal sealed class SettingsForm : Form
{
    private readonly DeckSettingsStore _store;

    public SettingsForm(DeckSettingsStore store)
    {
        _store = store;
        Text = "Pi Media Deck settings";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        Font = new Font("Segoe UI", 10f);
        Padding = new Padding(16);

        var layout = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            WrapContents = false,
            Dock = DockStyle.Fill,
        };
        layout.Controls.Add(new Label
        {
            Text = "Show on the deck",
            Font = new Font(Font.FontFamily, 12f, FontStyle.Bold),
            AutoSize = true,
            Margin = new Padding(0, 0, 0, 4),
        });
        layout.Controls.Add(new Label
        {
            Text = "The Volume tab is always there. Changes apply to the deck straight away.",
            ForeColor = SystemColors.GrayText,
            AutoSize = true,
            MaximumSize = new Size(380, 0),
            Margin = new Padding(0, 0, 0, 10),
        });

        Add(layout, "Spotify tab", "Now playing from Spotify on this PC.",
            s => s.Spotify, (s, v) => s with { Spotify = v });
        Add(layout, "Polarity tab", "While Polarity is open. Hidden, nothing about what you watch is sent to the deck.",
            s => s.Polarity, (s, v) => s with { Polarity = v });
        Add(layout, "Lights tab and light button", "The IKEA lights set up on the deck itself.",
            s => s.Lights, (s, v) => s with { Lights = v });
        Add(layout, "Discord sidebar", "Mute and deafen in the Volume tab during a Discord call.",
            s => s.Discord, (s, v) => s with { Discord = v });
        Add(layout, "Shut-down button", "The power button in the tab bar (always asks first).",
            s => s.Power, (s, v) => s with { Power = v });
        Add(layout, "Audio output switcher", "The output pill in the tab bar. Hidden, it only shows the connection.",
            s => s.Outputs, (s, v) => s with { Outputs = v });

        var close = new Button { Text = "Close", AutoSize = true, Margin = new Padding(0, 12, 0, 0) };
        close.Click += (_, _) => Close(); // modeless: DialogResult alone wouldn't close it
        layout.Controls.Add(close);
        AcceptButton = CancelButton = close;
        Controls.Add(layout);
    }

    private void Add(FlowLayoutPanel layout, string text, string hint,
        Func<DeckSettings, bool> get, Func<DeckSettings, bool, DeckSettings> set)
    {
        var box = new CheckBox { Text = text, Checked = get(_store.Current), AutoSize = true, Margin = new Padding(0, 6, 0, 0) };
        box.CheckedChanged += (_, _) => _store.Update(set(_store.Current, box.Checked));
        layout.Controls.Add(box);
        layout.Controls.Add(new Label
        {
            Text = hint,
            ForeColor = SystemColors.GrayText,
            AutoSize = true,
            MaximumSize = new Size(380, 0),
            Margin = new Padding(20, 0, 0, 4),
        });
    }
}
