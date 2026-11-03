using System.Runtime.InteropServices;

namespace PcCompanion.Audio;

/// <summary>
/// Switches the Windows default playback device. There is no public API for this; the Sound settings use the
/// undocumented IPolicyConfig COM interface, as do EarTrumpet and SoundSwitch. Its layout has been stable since
/// Windows 7. Only the vtable position of SetDefaultEndpoint matters, so the methods before it are placeholders.
/// </summary>
internal static class PolicyConfig
{
    private enum Role
    {
        Console = 0,
        Multimedia = 1,
        Communications = 2,
    }

    [ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
    private class CPolicyConfigClient
    {
    }

    [ComImport, Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPolicyConfig
    {
        [PreserveSig] int GetMixFormat();
        [PreserveSig] int GetDeviceFormat();
        [PreserveSig] int ResetDeviceFormat();
        [PreserveSig] int SetDeviceFormat();
        [PreserveSig] int GetProcessingPeriod();
        [PreserveSig] int SetProcessingPeriod();
        [PreserveSig] int GetShareMode();
        [PreserveSig] int SetShareMode();
        [PreserveSig] int GetPropertyValue();
        [PreserveSig] int SetPropertyValue();

        [PreserveSig]
        int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string deviceId, Role role);
    }

    /// <summary>Sets the device as default for all roles, like picking it in the Windows sound menu.</summary>
    public static void SetDefaultOutput(string deviceId)
    {
        var config = (IPolicyConfig)new CPolicyConfigClient();
        try
        {
            foreach (var role in new[] { Role.Console, Role.Multimedia, Role.Communications })
                Marshal.ThrowExceptionForHR(config.SetDefaultEndpoint(deviceId, role));
        }
        finally
        {
            Marshal.ReleaseComObject(config);
        }
    }
}
