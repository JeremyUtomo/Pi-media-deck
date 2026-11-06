using System.Diagnostics;
using System.Drawing.Drawing2D;
using Microsoft.Extensions.Logging;
using PcCompanion.Server;

namespace PcCompanion.Tray;

/// <summary>Tray icon showing whether a deck is connected, with Start with Windows, firewall and log options.</summary>
internal sealed class TrayApp : ApplicationContext
{
    private readonly DeckHub _hub;
    private readonly DeckSettingsStore _settings;
    private SettingsForm? _settingsForm;
    private readonly ILogger _log;
    private readonly int _port;
    private readonly string _logPath;
    private readonly NotifyIcon _notifyIcon;
    private readonly ToolStripMenuItem _status;
    private readonly ToolStripMenuItem _autostart;
    private readonly ToolStripMenuItem _firewall;
    private readonly Icon _connectedIcon = CreateIcon(Color.FromArgb(30, 215, 96));
    private readonly Icon _waitingIcon = CreateIcon(Color.FromArgb(150, 150, 150));
    private readonly SynchronizationContext _ui;

    public TrayApp(DeckHub hub, DeckSettingsStore settings, ILogger<TrayApp> log, int port, string logPath)
    {
        _hub = hub;
        _settings = settings;
        _log = log;
        _port = port;
        _logPath = logPath;

        _status = new ToolStripMenuItem { Enabled = false };
        _autostart = new ToolStripMenuItem("Start with Windows", null, OnAutostartClicked) { Checked = Autostart.IsEnabled };
        _firewall = new ToolStripMenuItem("Allow through firewall…", null, OnFirewallClicked);

        var menu = new ContextMenuStrip();
        menu.Items.AddRange([
            _status,
            new ToolStripSeparator(),
            new ToolStripMenuItem("Deck settings…", null, (_, _) => OpenSettings()),
            _autostart,
            _firewall,
            new ToolStripMenuItem("Open log", null, (_, _) => OpenLog()),
            new ToolStripSeparator(),
            new ToolStripMenuItem("Exit", null, (_, _) => ExitThread()),
        ]);
        menu.Opening += (_, _) => RefreshMenu();

        // Creating the first control installs the WinForms synchronization context on this thread.
        _ui = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();

        _notifyIcon = new NotifyIcon { ContextMenuStrip = menu, Visible = true };
        _hub.ClientsChanged += OnClientsChanged;
        ShowClients(_hub.ClientRemotes);
        RefreshMenu();
    }

    private void OnClientsChanged(IReadOnlyList<string> remotes) => _ui.Post(_ => ShowClients(remotes), null);

    private void ShowClients(IReadOnlyList<string> remotes)
    {
        var hosts = remotes.Select(r => r[..r.LastIndexOf(':')]).Distinct().ToList();
        var text = hosts.Count == 0 ? $"Waiting for deck (port {_port})" : $"Deck connected: {string.Join(", ", hosts)}";
        _status.Text = text;
        _notifyIcon.Text = $"Pi Media Deck — {text}";
        _notifyIcon.Icon = hosts.Count == 0 ? _waitingIcon : _connectedIcon;
    }

    private void RefreshMenu()
    {
        _autostart.Checked = Autostart.IsEnabled;
        _firewall.Visible = !Firewall.RuleExists();
    }

    private void OnAutostartClicked(object? sender, EventArgs e)
    {
        var enable = !Autostart.IsEnabled;
        Autostart.Set(enable);
        _log.LogInformation("Start with Windows: {State}", enable ? "on" : "off");
        RefreshMenu();
    }

    private void OnFirewallClicked(object? sender, EventArgs e)
    {
        var exitCode = Firewall.AddRule(_port);
        var added = Firewall.RuleExists();
        _log.LogInformation("Firewall rule: netsh {Result}; rule present afterwards: {Added}",
            exitCode is { } code ? $"exited with {code}" : "not run (UAC prompt declined)", added);
        if (!added)
            _notifyIcon.ShowBalloonTip(5000, "Pi Media Deck", "The firewall rule was not added. See the log.", ToolTipIcon.Warning);
        RefreshMenu();
    }

    private void OpenSettings()
    {
        if (_settingsForm is { IsDisposed: false })
        {
            _settingsForm.Activate();
            return;
        }
        _settingsForm = new SettingsForm(_settings);
        _settingsForm.FormClosed += (_, _) => _settingsForm = null;
        _settingsForm.Show();
    }

    private void OpenLog()
    {
        if (File.Exists(_logPath))
            Process.Start(new ProcessStartInfo(_logPath) { UseShellExecute = true });
    }

    /// <summary>A small rounded "screen" with a status dot, drawn at runtime so there are no icon assets.</summary>
    private static Icon CreateIcon(Color dot)
    {
        using var bitmap = new Bitmap(32, 32);
        using (var g = Graphics.FromImage(bitmap))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using var screen = new GraphicsPath();
            screen.AddArc(2, 6, 8, 8, 180, 90);
            screen.AddArc(22, 6, 8, 8, 270, 90);
            screen.AddArc(22, 18, 8, 8, 0, 90);
            screen.AddArc(2, 18, 8, 8, 90, 90);
            screen.CloseFigure();
            using var body = new SolidBrush(Color.FromArgb(235, 235, 235));
            g.FillPath(body, screen);
            using var glyph = new SolidBrush(Color.FromArgb(40, 40, 40));
            g.FillPolygon(glyph, new Point[] { new(12, 11), new(12, 21), new(20, 16) });
            using var status = new SolidBrush(dot);
            using var outline = new Pen(Color.FromArgb(20, 20, 20), 2);
            g.FillEllipse(status, 20, 20, 11, 11);
            g.DrawEllipse(outline, 20, 20, 11, 11);
        }
        return Icon.FromHandle(bitmap.GetHicon());
    }

    protected override void ExitThreadCore()
    {
        _notifyIcon.Visible = false;
        base.ExitThreadCore();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _hub.ClientsChanged -= OnClientsChanged;
            _notifyIcon.Dispose();
            _connectedIcon.Dispose();
            _waitingIcon.Dispose();
        }
        base.Dispose(disposing);
    }
}
