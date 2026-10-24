# Portable help only. No installer execution, activation or image production.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Owned = $null
$Created = $false

function Require($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Verify-Pin([long]$Bytes, [string]$Sha) {
    Require ($Bytes -gt 0 -and $Bytes -le 300000000 -and $Sha -cmatch '^[0-9a-f]{64}$') 'Invalid source pin'
}

function Verify-File([string]$Path, [long]$Bytes, [string]$Sha) {
    $file = Get-Item -LiteralPath $Path
    Require (-not $file.PSIsContainer -and -not ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) 'Nonregular input'
    Require ($file.Length -eq $Bytes) 'Input length differs'
    Require ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $Sha) 'Input SHA256 differs'
}

function Download-Fixed([string]$Destination) {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromMinutes(5)
    $response = $null; $inputStream = $null; $outputStream = $null
    try {
        $response = $client.GetAsync($Url, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        Require ($response.StatusCode -eq [Net.HttpStatusCode]::OK) 'Official download unavailable or redirected'
        $length = $response.Content.Headers.ContentLength
        Require ($null -eq $length -or $length -eq $InstallerBytes) 'HTTP length differs'
        $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $outputStream = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew)
        $buffer = [byte[]]::new(65536); [long]$total = 0
        $clock = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            $read = $inputStream.ReadAsync($buffer, 0, $buffer.Length)
            Require ($read.Wait(60000) -and $clock.Elapsed.TotalMinutes -lt 5) 'Official download timed out'
            $count = $read.GetAwaiter().GetResult()
            if ($count -eq 0) { break }
            $total += $count
            Require ($total -le $InstallerBytes) 'Download exceeds fixed size'
            $outputStream.Write($buffer, 0, $count)
        }
        Require ($total -eq $InstallerBytes) 'Truncated download'
    } finally {
        if ($null -ne $outputStream) { $outputStream.Dispose() }
        if ($null -ne $inputStream) { $inputStream.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
        $client.Dispose(); $handler.Dispose()
    }
    Verify-File $Destination $InstallerBytes $InstallerSha
}

function Extract-App([string]$Archive, [string]$Destination) {
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        Require ($zip.Entries.Count -gt 0 -and $zip.Entries.Count -le 10000) 'ZIP entry count differs'
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        [long]$total = 0
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            Require ($name -and -not $name.StartsWith('/') -and $name -notmatch '[:\x00-\x1f]' -and $name -notmatch '(^|/)\.\.?(/|$)') 'Unsafe ZIP path'
            Require ($seen.Add($name.TrimEnd('/'))) 'Duplicate ZIP entry'
            $type = ($entry.ExternalAttributes -shr 16) -band 0xf000
            Require ($type -in @(0, 0x8000, 0x4000)) 'ZIP link or unsupported type'
            $total += $entry.Length
            Require ($entry.Length -ge 0 -and $total -le 1073741824) 'ZIP expansion limit'
        }
        # The authenticated ZIP, rather than an invented executable hash, pins NeroCmd.
        $commands = @($zip.Entries | Where-Object { $_.FullName -ceq $CommandMember })
        Require ($commands.Count -eq 1 -and $commands[0].Length -gt 0) 'Missing/ambiguous NeroCmd entry'
        [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
        $command = Join-Path $Destination $commands[0].FullName
        $file = Get-Item -LiteralPath $command
        Require (-not $file.PSIsContainer -and -not ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $file.Length -eq $commands[0].Length) 'Extracted NeroCmd differs'
        foreach ($name in $Components.Keys) {
            Verify-File (Join-Path $Destination $name) $Components[$name][0] $Components[$name][1]
        }
        return $command
    } finally { $zip.Dispose() }
}

function Read-Help([string]$Command, [string]$Directory) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Command
    $info.ArgumentList.Add('--help')
    $info.WorkingDirectory = $Directory
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $info; $started = $false
    try {
        $started = $process.Start()
        Require $started 'Portable help process did not start'
        $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
        Require ($process.WaitForExit(60000)) 'Portable help timeout; dependencies/license/COM may be unavailable'
        Require ([Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdout, $stderr), 5000)) 'Portable help pipes did not close'
        $text = $stdout.GetAwaiter().GetResult(); $errors = $stderr.GetAwaiter().GetResult()
        Require ($text.Length + $errors.Length -le 1048576) 'Portable help output limit'
        Write-Output $text.Replace($Owned, '@temp')
        Write-Output $errors.Replace($Owned, '@temp')
        Require ($process.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($text)) 'Portable help failed; missing dependencies/license/COM are BLOCKED, without installer fallback'
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill($true); $process.WaitForExit() }
        $process.Dispose()
    }
}

try {
    Require ($IsWindows -and $env:RUNNER_OS -ceq 'Windows' -and $env:GITHUB_ACTIONS -ceq 'true') 'Hosted Windows probe required'
    Require ($env:RUNNER_TEMP -and [IO.Path]::IsPathFullyQualified($env:RUNNER_TEMP)) 'Absolute RUNNER_TEMP required'
    $lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot '../../plan/upstream.lock.json') -Raw | ConvertFrom-Json
    $pin = $lock.reference_tools.nero_2025
    Require ($pin.version -ceq '27.0.1000' -and $pin.url -ceq 'https://ecloud.nero.com/public.php/dav/files/pBxLJzgPaNtAJBi') 'Unsupported official source'
    Require ($pin.filename -cmatch '^[A-Za-z0-9_.-]+\.exe$' -and $pin.payload.path -ceq 'NeroInstaller/NeroBurningROM.zip') 'Unsupported archive layout'
    Require ($pin.command_path -ceq 'Nero Burning ROM/NeroCmd.exe') 'Unsupported command layout'
    $CommandMember = $pin.command_path
    $Url = $pin.url; $InstallerBytes = [long]$pin.bytes; $InstallerSha = $pin.sha256
    $ZipMember = $pin.payload.path; $ZipBytes = [long]$pin.payload.bytes; $ZipSha = $pin.payload.sha256
    $Components = @{}
    foreach ($component in $pin.components) {
        Require ($component.path -cin @('Nero Burning ROM/NeroAPIEngine.dll', 'Nero Burning ROM/uimage.dll', 'Nero Burning ROM/uImageGen.dll') -and -not $Components.ContainsKey($component.path)) 'Unexpected/duplicate component'
        $Components[$component.path] = @([long]$component.bytes, $component.sha256)
    }
    Require ($Components.Count -eq 3) 'Missing component pins'
    Verify-Pin $InstallerBytes $InstallerSha
    Verify-Pin $ZipBytes $ZipSha
    foreach ($name in $Components.Keys) { Verify-Pin $Components[$name][0] $Components[$name][1] }
    $seven = Get-Command '7z.exe' -CommandType Application -ErrorAction Stop
    $parent = (Get-Item -LiteralPath $env:RUNNER_TEMP).FullName
    Require (-not ((Get-Item -LiteralPath $parent).Attributes -band [IO.FileAttributes]::ReparsePoint)) 'Linked RUNNER_TEMP'
    $Owned = Join-Path $parent ('p6-nero-probe-' + [Guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $Owned
    $Created = $true
    $installer = Join-Path $Owned $pin.filename
    Download-Fixed $installer
    $unpacked = Join-Path $Owned 'unpacked'
    & $seven.Source x $installer $ZipMember ('-o' + $unpacked) -y
    Require ($LASTEXITCODE -eq 0) 'Embedded ZIP extraction failed'
    $archive = Join-Path $unpacked $ZipMember
    Verify-File $archive $ZipBytes $ZipSha
    $app = Join-Path $Owned 'app'
    $command = Extract-App $archive $app
    Read-Help $command ([IO.Path]::GetDirectoryName($command))
    Write-Output 'P6_NERO_PROBE PORTABLE_HELP_ONLY; no discovery, TOC or image production attempted'
} catch {
    $message = $_.Exception.Message
    if ($Owned) { $message = $message.Replace($Owned, '@temp') }
    Write-Output ('P6_NERO_PROBE BLOCKED: ' + $message)
    exit 2
} finally {
    if ($Created -and (Test-Path -LiteralPath $Owned)) {
        Remove-Item -LiteralPath $Owned -Recurse -Force
    }
}
