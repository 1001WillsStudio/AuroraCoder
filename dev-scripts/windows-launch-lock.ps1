param(
    [Parameter(Mandatory = $true)][string]$BatchPath,
    [string]$Instance = ''
)

if ($Instance -and $Instance -notmatch '^[0-9]+$') {
    Write-Error 'Provide a numeric instance number.'
    exit 1
}

$gate = [System.Threading.Mutex]::new($false, 'Local\AuroraCoder.Dev.ContainerLaunch')
$held = $false
$result = 1
try {
    try {
        $held = $gate.WaitOne(0)
        if (-not $held) {
            Write-Host 'Waiting for the active AuroraCoder launch...'
            $held = $gate.WaitOne([TimeSpan]::FromMinutes(15))
        }
    } catch [System.Threading.AbandonedMutexException] {
        $held = $true
    }
    if (-not $held) { throw 'Another AuroraCoder launch has not finished.' }

    $child = [System.Diagnostics.ProcessStartInfo]::new()
    $child.FileName = $env:ComSpec
    $child.Arguments = '/d /v:off /s /c ""%AURORA_DEV_LAUNCH_BATCH%"'
    if ($Instance) { $child.Arguments += ' %AURORA_DEV_LAUNCH_INSTANCE%' }
    $child.Arguments += '"'
    $child.UseShellExecute = $false
    $child.EnvironmentVariables['AURORA_DEV_LAUNCH_BATCH'] = $BatchPath
    $child.EnvironmentVariables['AURORA_DEV_LAUNCH_INSTANCE'] = $Instance
    $child.EnvironmentVariables['AURORA_DEV_LAUNCH_ID'] = [Guid]::NewGuid().ToString('N')
    $process = [System.Diagnostics.Process]::Start($child)
    try {
        $process.WaitForExit()
        $result = $process.ExitCode
    } finally {
        $process.Dispose()
    }
} catch {
    Write-Error $_
} finally {
    if ($held) { $gate.ReleaseMutex() }
    $gate.Dispose()
}
exit $result
