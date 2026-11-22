param([switch]$Desinstalar)

$ErrorActionPreference = 'Stop'

function Pause-End($code) {
    Write-Host ""
    Read-Host "Aperte Enter para fechar"
    exit $code
}

function Write-JsonNoBom($path, $obj) {
    $json = $obj | ConvertTo-Json -Depth 50
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

try {
    $root = Split-Path -Parent $PSScriptRoot
    $driverDir = Join-Path $root 'joyconvr'

    if (-not $Desinstalar -and -not (Test-Path (Join-Path $driverDir 'bin\win64\driver_joyconvr.dll'))) {
        Write-Host "Nao achei a pasta 'joyconvr' ao lado deste instalador. Extraia o .zip inteiro primeiro." -ForegroundColor Red
        Pause-End 1
    }

    $running = Get-Process -Name vrserver, vrmonitor, vrcompositor -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "O SteamVR esta aberto. Feche o SteamVR e rode de novo." -ForegroundColor Yellow
        Pause-End 1
    }

    $vrpathFile = Join-Path $env:LOCALAPPDATA 'openvr\openvrpaths.vrpath'
    if (-not (Test-Path $vrpathFile)) {
        Write-Host "Nao achei o SteamVR. Instale o SteamVR pela Steam e abra ele uma vez antes." -ForegroundColor Red
        Pause-End 1
    }
    $vrpaths = Get-Content $vrpathFile -Raw | ConvertFrom-Json
    $steamvr = @($vrpaths.runtime)[0]
    $steamConfig = @($vrpaths.config)[0]
    $vrpathreg = Join-Path $steamvr 'bin\win64\vrpathreg.exe'
    $settingsFile = Join-Path $steamConfig 'steamvr.vrsettings'

    Write-Host "SteamVR: $steamvr"
    Write-Host "Configuracao: $settingsFile"

    # --- Registrar / remover o driver
    if (Test-Path $vrpathreg) {
        if ($Desinstalar) {
            & $vrpathreg removedriver $driverDir | Out-Null
        } else {
            & $vrpathreg adddriver $driverDir | Out-Null
        }
    } else {
        throw "vrpathreg.exe nao encontrado em $vrpathreg"
    }

    # --- Ajustar steamvr.vrsettings
    if (Test-Path $settingsFile) {
        $backup = "$settingsFile.antes-do-joyconvr"
        if (-not $Desinstalar -and -not (Test-Path $backup)) {
            Copy-Item $settingsFile $backup
            Write-Host "Backup criado: $backup"
        }
        $settings = Get-Content $settingsFile -Raw | ConvertFrom-Json
    } else {
        $settings = New-Object PSObject
    }

    if (-not ($settings.PSObject.Properties.Name -contains 'steamvr')) {
        $settings | Add-Member -NotePropertyName 'steamvr' -NotePropertyValue (New-Object PSObject)
    }
    $sv = $settings.steamvr

    if ($Desinstalar) {
        if ($sv.PSObject.Properties.Name -contains 'forcedDriver' -and $sv.forcedDriver -eq 'joyconvr') {
            $sv.PSObject.Properties.Remove('forcedDriver')
        }
        if ($sv.PSObject.Properties.Name -contains 'activateMultipleDrivers') {
            $sv.PSObject.Properties.Remove('activateMultipleDrivers')
        }
    } else {
        $sv | Add-Member -NotePropertyName 'forcedDriver' -NotePropertyValue 'joyconvr' -Force
        $sv | Add-Member -NotePropertyName 'activateMultipleDrivers' -NotePropertyValue $true -Force
        $sv | Add-Member -NotePropertyName 'requireHmd' -NotePropertyValue $true -Force
    }

    # Garantir que o driver nao fica desativado
    if (-not $Desinstalar) {
        $section = 'driver_joyconvr'
        if (-not ($settings.PSObject.Properties.Name -contains $section)) {
            $settings | Add-Member -NotePropertyName $section -NotePropertyValue (New-Object PSObject)
        }
        $settings.$section | Add-Member -NotePropertyName 'enable' -NotePropertyValue $true -Force
        $settings.$section | Add-Member -NotePropertyName 'blocked_by_safe_mode' -NotePropertyValue $false -Force
    }

    # O headset virtual nunca se mexe, entao o SteamVR acharia que ele foi tirado da
    # cabeca e entraria em modo de espera (tela preta). Desliga o modo de espera.
    if (-not ($settings.PSObject.Properties.Name -contains 'power')) {
        $settings | Add-Member -NotePropertyName 'power' -NotePropertyValue (New-Object PSObject)
    }
    $pw = $settings.power
    $powerKeys = 'pauseCompositorOnStandby', 'turnOffScreensTimeout', 'turnOffControllersTimeout', 'autoLaunchSteamVROnButtonPress'
    if ($Desinstalar) {
        foreach ($k in $powerKeys) {
            if ($pw.PSObject.Properties.Name -contains $k) { $pw.PSObject.Properties.Remove($k) }
        }
    } else {
        $pw | Add-Member -NotePropertyName 'pauseCompositorOnStandby' -NotePropertyValue $false -Force
        $pw | Add-Member -NotePropertyName 'turnOffScreensTimeout' -NotePropertyValue 86400.0 -Force
        $pw | Add-Member -NotePropertyName 'turnOffControllersTimeout' -NotePropertyValue 0.0 -Force
        $pw | Add-Member -NotePropertyName 'autoLaunchSteamVROnButtonPress' -NotePropertyValue $false -Force
    }

    Write-JsonNoBom $settingsFile $settings

    if ($Desinstalar) {
        Write-Host ""
        Write-Host "JoyconVR removido. O SteamVR voltou ao normal." -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "Pronto! JoyconVR instalado." -ForegroundColor Green
        Write-Host "1) Joy-Con do Switch 1: conecte pelo Bluetooth do Windows"
        Write-Host "   Joy-Con 2 do Switch 2: abra JoyconVR-Joycon2.exe (veja o LEIA-ME)"
        Write-Host "2) Abra o SteamVR"
        Write-Host "3) Segure os Joy-Cons apontando para a tela e segure o botao Captura (quadradinho) por 1 segundo"
        Write-Host ""
        Write-Host "IMPORTANTE: nao mova nem apague esta pasta: $root" -ForegroundColor Yellow
    }
    Pause-End 0
}
catch {
    Write-Host ""
    Write-Host "Deu erro: $($_.Exception.Message)" -ForegroundColor Red
    Pause-End 1
}
