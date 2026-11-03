using System.ComponentModel;
using System.Diagnostics;

namespace PcCompanion.Tray;

/// <summary>
/// An inbound rule for the server port, independent of where the exe lives. Allowed on Private and Public
/// profiles (this PC's Wi-Fi is classified Public) but only from the local subnet.
/// </summary>
internal static class Firewall
{
    public const string RuleName = "Pi Media Deck (TCP 8765)";

    public static bool RuleExists()
    {
        try
        {
            using var process = Process.Start(new ProcessStartInfo("netsh", $"advfirewall firewall show rule name=\"{RuleName}\"")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
            })!;
            process.StandardOutput.ReadToEnd();
            process.WaitForExit();
            return process.ExitCode == 0;
        }
        catch (Win32Exception)
        {
            return false;
        }
    }

    /// <summary>Adds the rule through an elevated netsh (UAC prompt). Returns netsh's exit code, or null if the prompt was declined.</summary>
    public static int? AddRule(int port)
    {
        try
        {
            using var process = Process.Start(new ProcessStartInfo(
                "netsh",
                $"advfirewall firewall add rule name=\"{RuleName}\" dir=in action=allow protocol=TCP localport={port} " +
                "remoteip=localsubnet profile=private,public")
            {
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden,
            })!;
            process.WaitForExit();
            return process.ExitCode;
        }
        catch (Win32Exception)
        {
            return null; // UAC prompt cancelled
        }
    }
}
