
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Say([string]$Message) {
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Get-Utf8Name([string]$Base64) {
    $bytes = [Convert]::FromBase64String($Base64)
    return [Text.Encoding]::UTF8.GetString($bytes)
}

function Find-ISCC {
    $candidates = [System.Collections.Generic.List[string]]::new()

    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        $candidates.Add($cmd.Source)
    }

    $roots = @(
        (Join-Path $env:LOCALAPPDATA 'Programs'),
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        (Join-Path $env:LOCALAPPDATA 'Apps')
    ) | Where-Object { $_ -and (Test-Path $_) }

    foreach ($base in $roots) {
        foreach ($folder in @('Inno Setup 7', 'Inno Setup 6', 'Inno Setup')) {
            $candidate = Join-Path $base (Join-Path $folder 'ISCC.exe')
            if (Test-Path $candidate) {
                $candidates.Add($candidate)
            }
        }

        try {
            Get-ChildItem -Path $base -Directory -Filter 'Inno Setup*' -ErrorAction SilentlyContinue |
                ForEach-Object {
                    $candidate = Join-Path $_.FullName 'ISCC.exe'
                    if (Test-Path $candidate) {
                        $candidates.Add($candidate)
                    }
                }
        } catch {}
    }

    foreach ($key in @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )) {
        try {
            Get-ItemProperty $key -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.DisplayName -like 'Inno Setup*' -and $_.InstallLocation
                } |
                ForEach-Object {
                    $candidate = Join-Path $_.InstallLocation 'ISCC.exe'
                    if (Test-Path $candidate) {
                        $candidates.Add($candidate)
                    }
                }
        } catch {}
    }

    return ($candidates | Select-Object -Unique |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1)
}

function Invoke-Compiler([string]$Compiler, [string]$Script) {
    $old = $ErrorActionPreference

    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Compiler /Qp $Script 2>&1
        $code = $LASTEXITCODE

        foreach ($line in $output) {
            Write-Host $line
        }
    } finally {
        $ErrorActionPreference = $old
    }

    if ($code -ne 0) {
        throw "Inno Setup failed with exit code $code."
    }
}

function Install-InnoSetup {
    $installer = Join-Path $env:TEMP 'SchoolPass_InnoSetup_Installer.exe'
    $url = 'https://jrsoftware.org/download.php/is.exe'

    Say 'Downloading official Inno Setup installer'

    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer -TimeoutSec 90

    if (-not (Test-Path $installer) -or (Get-Item $installer).Length -lt 1000000) {
        throw 'The downloaded Inno Setup installer is missing or smaller than expected.'
    }

    $signature = Get-AuthenticodeSignature -FilePath $installer

    if ($signature.Status -ne 'Valid') {
        throw "Inno Setup signature is invalid. Status: $($signature.Status)"
    }

    $proc = Start-Process -FilePath $installer -ArgumentList @(
        '/CURRENTUSER',
        '/VERYSILENT',
        '/SUPPRESSMSGBOXES',
        '/NORESTART',
        '/SP-'
    ) -Wait -PassThru

    if ($proc.ExitCode -ne 0) {
        throw "Inno Setup installer exited with code $($proc.ExitCode)."
    }

    Start-Sleep -Seconds 2
}

try {
    Say 'Checking portable application files'

    $appExe = Join-Path $Root 'dist\SchoolPass\SchoolPass.exe'
    $iss = Join-Path $Root 'SchoolPass_Setup.iss'

    if (-not (Test-Path $appExe) -or (Get-Item $appExe).Length -lt 100000) {
        throw 'Valid dist\SchoolPass\SchoolPass.exe was not found. Build the portable application first.'
    }

    if (-not (Test-Path $iss)) {
        throw 'SchoolPass_Setup.iss was not found.'
    }

    # Required asset names are decoded from UTF-8 Base64.
    # This keeps the PowerShell source file ASCII-only.
    $assets = @(
        'VERSION.txt',
        (Get-Utf8Name '2YLYp9mE2Khf2YLYp9im2YXYqV/Yp9mE2KrZhNin2YXZitiwLmNzdg=='),
        (Get-Utf8Name '2K/ZhNmK2YRf2KfZhNin2LPYqtiu2K/Yp9mFLnR4dA=='),
        (Get-Utf8Name '2YHYqtitX9in2YTZhdmG2YHYsF/ZhNix2YXZiNiyX1FSXyjZg9mF2K/ZitixKS5iYXQ=')
    )

    foreach ($asset in $assets) {
        $assetPath = Join-Path $Root $asset

        if (-not (Test-Path $assetPath)) {
            throw "Required installer asset is missing: $asset"
        }
    }

    Say 'Locating Inno Setup compiler'

    $compiler = Find-ISCC

    if (-not $compiler) {
        try {
            Install-InnoSetup
        } catch {
            throw "Automatic Inno Setup installation failed: $($_.Exception.Message). Install Inno Setup 6 or 7 from the official website."
        }

        $compiler = Find-ISCC
    }

    if (-not $compiler) {
        throw 'ISCC.exe was not found after installation.'
    }

    Write-Host "Inno Setup compiler: $compiler"

    $output = Join-Path $Root 'dist\SchoolPass_Setup.exe'

    if (Test-Path $output) {
        Remove-Item $output -Force
    }

    $started = Get-Date

    Say 'Compiling Windows installer'

    Invoke-Compiler $compiler $iss

    Say 'Validating installer'

    if (-not (Test-Path $output)) {
        throw 'ISCC finished without creating dist\SchoolPass_Setup.exe.'
    }

    $file = Get-Item $output

    if ($file.Length -lt 1000000) {
        throw "Installer is incomplete or too small: $($file.Length) bytes."
    }

    if ($file.LastWriteTime -lt $started.AddSeconds(-2)) {
        throw 'Installer timestamp does not confirm that it was created during this build.'
    }

    $stream = [System.IO.File]::OpenRead($output)

    try {
        $header = New-Object byte[] 2
        [void]$stream.Read($header, 0, 2)

        if ($header[0] -ne 0x4D -or $header[1] -ne 0x5A) {
            throw 'The generated file does not have a valid Windows EXE header.'
        }
    } finally {
        $stream.Dispose()
    }

    $hash = (Get-FileHash -Algorithm SHA256 -Path $output).Hash.ToLowerInvariant()

    Set-Content -Path ($output + '.sha256') `
        -Value ("$hash  SchoolPass_Setup.exe") `
        -Encoding ASCII

    Write-Host "`nSUCCESS: Installer created and validated." -ForegroundColor Green
    Write-Host "Output: $output" -ForegroundColor Green
    Write-Host "SHA256: $hash" -ForegroundColor Green

    exit 0

} catch {
    Write-Host "`nInstaller creation failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Portable application files and database were not deleted.' -ForegroundColor Yellow
    exit 1
}
