using System.Diagnostics;
using System.Security;
using System.Security.Principal;
using System.Text;
using Microsoft.Win32;

namespace PcCompanion.Tray;

/// <summary>"Start with Windows" as a per-user Task Scheduler task with a logon trigger (no admin needed).</summary>
/// <remarks>
/// The HKCU Run key was tried first: on this Windows 11 build Explorer silently skipped the entry at sign-in,
/// even with an "enabled" StartupApproved value identical to apps it did run (Shell-Core log, two restarts).
/// A logon task is what Spotify uses here and starts reliably. Leftover Run/approval values are removed.
/// </remarks>
internal static class Autostart
{
    private const string TaskName = "PiMediaDeck";
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ApprovedKey = @"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run";
    private const string LegacyValueName = "PiMediaDeck";

    private static string ExePath => Environment.ProcessPath!;

    /// <summary>True if the logon task exists and starts this executable (not an older copy elsewhere).</summary>
    public static bool IsEnabled
    {
        get
        {
            var (exitCode, xml) = Schtasks($"/Query /TN \"{TaskName}\" /XML");
            return exitCode == 0 && xml.Contains(SecurityElement.Escape(ExePath), StringComparison.OrdinalIgnoreCase);
        }
    }

    public static void Set(bool enabled)
    {
        RemoveLegacyRunEntry();
        if (!enabled)
        {
            Schtasks($"/Delete /TN \"{TaskName}\" /F");
            return;
        }

        var file = Path.Combine(Path.GetTempPath(), "PiMediaDeck-task.xml");
        File.WriteAllText(file, TaskXml(), Encoding.Unicode);
        try
        {
            var (exitCode, output) = Schtasks($"/Create /TN \"{TaskName}\" /XML \"{file}\" /F");
            if (exitCode != 0)
                throw new InvalidOperationException($"schtasks /Create failed ({exitCode}): {output.Trim()}");
        }
        finally
        {
            File.Delete(file);
        }
    }

    private static string TaskXml()
    {
        var user = SecurityElement.Escape(WindowsIdentity.GetCurrent().Name);
        var exe = SecurityElement.Escape(ExePath);
        return $"""
            <?xml version="1.0" encoding="UTF-16"?>
            <Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
              <RegistrationInfo><Description>Pi Media Deck companion: WebSocket server for the touchscreen deck.</Description></RegistrationInfo>
              <Triggers>
                <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId><Delay>PT5S</Delay></LogonTrigger>
              </Triggers>
              <Principals>
                <Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
              </Principals>
              <Settings>
                <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
                <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
                <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
                <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
                <Priority>5</Priority>
              </Settings>
              <Actions Context="Author"><Exec><Command>{exe}</Command></Exec></Actions>
            </Task>
            """;
    }

    private static void RemoveLegacyRunEntry()
    {
        using (var run = Registry.CurrentUser.OpenSubKey(RunKey, writable: true))
            run?.DeleteValue(LegacyValueName, throwOnMissingValue: false);
        using (var approved = Registry.CurrentUser.OpenSubKey(ApprovedKey, writable: true))
            approved?.DeleteValue(LegacyValueName, throwOnMissingValue: false);
    }

    private static (int ExitCode, string Output) Schtasks(string arguments)
    {
        using var process = Process.Start(new ProcessStartInfo("schtasks.exe", arguments)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        })!;
        var output = process.StandardOutput.ReadToEnd() + process.StandardError.ReadToEnd();
        process.WaitForExit();
        return (process.ExitCode, output);
    }
}
