# Install killer-skills into ~/.claude/skills, from the latest release.
#
#   irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1 | iex
#
# Name skills to install only those:
#
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1))) killer-repo
#
# Each archive is checked against the release's SHA256SUMS.txt before anything
# is replaced. A skill that's already installed is replaced whole, so files a
# newer version dropped don't linger. Windows PowerShell 5.1 and PowerShell 7.
#
# On Windows, when killer-design or killer-repo is installed, it also installs the
# runtime (Playwright, the Chromium it launches, and Mermaid) into ~/.cache/killer-skills,
# unless that's already there or killer-design already has Playwright and its Chromium.
# With it, those two skills download nothing on first use.
#
#   $env:KILLER_SKILLS_VERSION = 'v1.0.0'   a release tag instead of the latest
#   $env:KILLER_SKILLS_DIR = 'path'         install somewhere other than ~/.claude/skills
#   $env:KILLER_SKILLS_RUNTIME = 'skip'     don't install the runtime
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Skill)

function Install-KillerSkills {
  param([string[]]$Skill)
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'   # the progress bar makes downloads crawl in 5.1
  if ($PSVersionTable.PSVersion.Major -lt 6) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  }

  $repo = 'Real-Fruit-Snacks/killer-skills'
  $bundle = 'killer-skills'
  $version = if ($env:KILLER_SKILLS_VERSION) { $env:KILLER_SKILLS_VERSION } else { 'latest' }
  $base = if ($version -eq 'latest') { "https://github.com/$repo/releases/latest/download" } else { "https://github.com/$repo/releases/download/$version" }
  if ($env:KILLER_SKILLS_URL) { $base = $env:KILLER_SKILLS_URL.TrimEnd('/') }   # for tests: release files served over http
  $dest = if ($env:KILLER_SKILLS_DIR) { $env:KILLER_SKILLS_DIR } else { Join-Path (Join-Path $HOME '.claude') 'skills' }

  # Retries cover GitHub's occasional 5xx on release downloads; a 4xx won't change, so it fails at once.
  function Get-File([string]$Url, [string]$Path) {
    for ($try = 1; ; $try++) {
      try { Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing; return }
      catch {
        $code = 0
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        if ($try -ge 5 -or ($code -ge 400 -and $code -lt 500)) { throw "failed: could not download ${Url}: $($_.Exception.Message)" }
        Start-Sleep -Seconds (2 * $try)
      }
    }
  }

  # Hashing and unzipping go through .NET, not Get-FileHash and Expand-Archive: Windows
  # PowerShell started from PowerShell 7 inherits 7's module path and can't load those.
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  function Get-Sha256([string]$Path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
  }

  function Show-Path([string]$Path) {
    if ($Path.StartsWith($HOME + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { return '~' + $Path.Substring($HOME.Length) }
    return $Path
  }

  # killer-design's own Playwright, with the Chromium it launches: its version, or $null.
  function Get-PlaywrightReady([string]$Base) {
    $pkg = Join-Path $Base 'node_modules\playwright\package.json'
    $browsers = Join-Path $Base 'node_modules\playwright-core\browsers.json'
    if (-not (Test-Path -LiteralPath $pkg) -or -not (Test-Path -LiteralPath $browsers)) { return $null }
    try {
      $shell = (Get-Content -Raw -LiteralPath $browsers | ConvertFrom-Json).browsers | Where-Object { $_.name -eq 'chromium-headless-shell' } | Select-Object -First 1
      $root = if ($env:PLAYWRIGHT_BROWSERS_PATH) { $env:PLAYWRIGHT_BROWSERS_PATH } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'ms-playwright' } else { $null }
      if ($shell -and $root -and (Test-Path -LiteralPath (Join-Path $root "chromium_headless_shell-$($shell.revision)\INSTALLATION_COMPLETE"))) {
        return (Get-Content -Raw -LiteralPath $pkg | ConvertFrom-Json).version
      }
    } catch {}
    return $null
  }

  $runtimeZip = 'killer-skills-runtime-windows-x64.zip'
  $needsRuntime = @('killer-design', 'killer-repo')   # the skills that drive Chromium
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('killer-skills-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmp | Out-Null
  $stage = $null
  $runtimeStage = $null
  try {
    $sumsFile = Join-Path $tmp 'SHA256SUMS.txt'
    Get-File "$base/SHA256SUMS.txt" $sumsFile
    $sums = @{}
    foreach ($line in Get-Content $sumsFile) {
      if ($line -match '^([0-9a-f]{64})  (.+)$') { $sums[$Matches[2]] = $Matches[1] }
    }
    $available = @($sums.Keys | Where-Object { $_ -like '*.zip' } | ForEach-Object { $_ -replace '\.zip$', '' } |
      Where-Object { $_ -ne $bundle -and $_ -notlike "$bundle-runtime-*" } | Sort-Object)
    if (-not $available) { throw "failed: no skills listed in $base/SHA256SUMS.txt" }
    $names = @($Skill | Where-Object { $_ })
    if (-not $names) { $names = $available }
    foreach ($name in $names) {
      if ($name -cnotmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { throw "failed: '$name' isn't a skill name. Available: $($available -join ' ')" }
      if ($available -cnotcontains $name) { throw "failed: no skill called $name in this release. Available: $($available -join ' ')" }
    }

    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    # Unpack beside the target, so the final step is a rename on the same drive.
    $stage = Join-Path $dest ('.killer-skills-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    $count = 0
    foreach ($name in $names) {
      $zip = Join-Path $tmp "$name.zip"
      Get-File "$base/$name.zip" $zip
      if ((Get-Sha256 $zip) -ne $sums["$name.zip"]) { throw "failed: $name.zip doesn't match SHA256SUMS.txt; nothing was changed for $name" }
      $x = Join-Path $stage $name
      [IO.Compression.ZipFile]::ExtractToDirectory($zip, $x)
      if (-not (Test-Path -LiteralPath (Join-Path (Join-Path $x $name) 'SKILL.md'))) { throw "failed: $name.zip has no $name/SKILL.md" }
      $target = Join-Path $dest $name
      if (Test-Path -LiteralPath $target) { $verb = 'updated'; Remove-Item -LiteralPath $target -Recurse -Force } else { $verb = 'installed' }
      Move-Item -LiteralPath (Join-Path $x $name) -Destination $target
      Write-Host "$verb $name"
      $count++
    }

    $onWindows = ($PSVersionTable.PSVersion.Major -lt 6) -or $IsWindows
    $wanted = @($names | Where-Object { $needsRuntime -contains $_ }).Count -gt 0
    if ($wanted -and $onWindows -and $sums.ContainsKey($runtimeZip) -and $env:KILLER_SKILLS_RUNTIME -ne 'skip') {
      $caches = if ($env:KILLER_SKILLS_CACHE) { $env:KILLER_SKILLS_CACHE } else { Join-Path $HOME '.cache' }
      $runtime = Join-Path $caches 'killer-skills'
      $marker = Join-Path $runtime 'installed-sha256.txt'
      $design = Join-Path $caches 'killer-design'
      $existing = Get-PlaywrightReady $design
      if ((Test-Path -LiteralPath $marker) -and ((Get-Content -Raw -LiteralPath $marker).Trim() -eq $sums[$runtimeZip])) {
        Write-Host "runtime already in $(Show-Path $runtime)"
      }
      elseif (-not (Test-Path -LiteralPath $runtime) -and $existing) {
        Write-Host "skipped the runtime: Playwright $existing and its Chromium are already in $(Show-Path $design)"
      }
      else {
        Write-Host 'downloading the runtime: Playwright, Chromium and Mermaid ...'
        $rz = Join-Path $tmp $runtimeZip
        Get-File "$base/$runtimeZip" $rz
        if ((Get-Sha256 $rz) -ne $sums[$runtimeZip]) { throw "failed: $runtimeZip doesn't match SHA256SUMS.txt; the runtime wasn't changed" }
        New-Item -ItemType Directory -Force -Path $caches | Out-Null
        # Unpack beside the target, so the final step is a rename on the same drive.
        $runtimeStage = Join-Path $caches ('.killer-skills-runtime-' + [guid]::NewGuid().ToString('N'))
        [IO.Compression.ZipFile]::ExtractToDirectory($rz, $runtimeStage)
        if (-not (Test-Path -LiteralPath (Join-Path $runtimeStage 'runtime.json'))) { throw "failed: $runtimeZip has no runtime.json" }
        Set-Content -LiteralPath (Join-Path $runtimeStage 'installed-sha256.txt') -Value $sums[$runtimeZip] -Encoding ascii
        if (Test-Path -LiteralPath $runtime) { $verb = 'updated'; Remove-Item -LiteralPath $runtime -Recurse -Force } else { $verb = 'installed' }
        Move-Item -LiteralPath $runtimeStage -Destination $runtime
        $runtimeStage = $null
        $info = Get-Content -Raw -LiteralPath (Join-Path $runtime 'runtime.json') | ConvertFrom-Json
        Write-Host "$verb the runtime: Playwright $($info.playwright), Chrome $($info.chrome) and Mermaid $($info.mermaid), in $(Show-Path $runtime)"
      }
    }

    $shown = Show-Path $dest
    if ($count -eq 1) { $noun = 'skill'; $them = 'it' } else { $noun = 'skills'; $them = 'them' }
    Write-Host "$count $noun in $shown"
    Write-Host "open a new Claude Code session to use $them"
  }
  finally {
    if ($stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
    if ($runtimeStage) { Remove-Item -LiteralPath $runtimeStage -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
}

Install-KillerSkills -Skill $Skill
