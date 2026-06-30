@{
    ModuleVersion     = '1.0.0'
    GUID              = 'a7e8f9d0-c1b2-4e3d-a5f6-7b8c9d0e1f2a'
    Author            = 'Seat AOI Team'
    Description       = 'Shared deployment utilities for Seat Surface AOI Windows station'
    PowerShellVersion = '5.1'
    FunctionsToExport = @(
        'Assert-PythonModulesAvailable',
        'Assert-PythonVersionSupported',
        'Get-DisplayPython',
        'Get-UvPackageIndexArguments',
        'Get-VenvPython',
        'Invoke-Native',
        'Invoke-NativeOptional',
        'Invoke-NativeQuiet',
        'Quote-Argument',
        'Remove-ServiceIfExists',
        'Resolve-DefaultRootOnProjectDrive',
        'Resolve-DeploymentRoot',
        'Resolve-Nssm',
        'Resolve-ProjectRoot',
        'Test-IsAdministrator',
        'Test-PlaceholderFile',
        'Wait-ServiceStopped'
    )
    RootModule        = 'SeatAoiDeployment.psm1'
}
