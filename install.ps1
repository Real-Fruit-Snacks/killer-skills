# Install killer-skills into ~/.claude/skills, from the latest release.
#
#   irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1 | iex
#
# Name skills to install only those:
#
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1))) killer-repo
#
# Offline: download the release's files into one folder (this script, SHA256SUMS.txt and
# the .zip files) and run it from there. It installs from that folder and downloads nothing:
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 [skill ...]
#
# Each archive is checked against the release's SHA256SUMS.txt before anything is
# replaced. A skill that's already installed is replaced whole, so files a newer version
# dropped don't linger. Windows PowerShell 5.1 and PowerShell 7.
#
# Beside the skills it installs the packs they use, each into its own folder in ~/.cache,
# only when it's missing or out of date:
#   killer-skills         Windows: Playwright, its Chromium and Mermaid, for killer-design,
#                         killer-repo and killer-slides (not when killer-design already has
#                         Playwright and Chromium)
#   killer-skills-fonts   the web fonts killer-design suggests, to self-host
#   killer-skills-node    Windows: Node.js, for killer-design, killer-repo and killer-slides, if
#                         there's no Node 20+
#   killer-skills-python  Windows: Python, for the skills whose scripts are Python (killer-council,
#                         killer-bugs, killer-tests, killer-docs, killer-perf, killer-release,
#                         killer-upgrade and killer-goal), if there's no Python 3.10+
#
#   $env:KILLER_SKILLS_VERSION = 'v1.0.0'   a release tag instead of the latest
#   $env:KILLER_SKILLS_DIR = 'path'         install somewhere other than ~/.claude/skills
#   $env:KILLER_SKILLS_RUNTIME = 'skip'     the skills only, no packs
#   $env:KILLER_SKILLS_RUNTIME = 'all'      every pack the skills use, even with Node, Python or Playwright here
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Skill)

function Install-KillerSkills {
  param([string[]]$Skill, [string]$From)
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
  # Run as a file from a folder of this release's files (its SHA256SUMS.txt lists the
  # bundle), it installs from that folder.
  $localSums = if ($From) { Join-Path $From 'SHA256SUMS.txt' } else { $null }
  $local = [bool]($localSums -and (Test-Path -LiteralPath $localSums) -and
    (Select-String -LiteralPath $localSums -Pattern "^[0-9a-f]{64}  $bundle\.zip$" -Quiet))
  if ($local) { $base = $From }
  $dest = if ($env:KILLER_SKILLS_DIR) { $env:KILLER_SKILLS_DIR } else { Join-Path (Join-Path $HOME '.claude') 'skills' }
  $caches = if ($env:KILLER_SKILLS_CACHE) { $env:KILLER_SKILLS_CACHE } else { Join-Path $HOME '.cache' }
  # Full paths, since the .NET calls below resolve relative ones against a different folder than PowerShell's.
  $dest = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($dest)
  $caches = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($caches)
  $mode = "$env:KILLER_SKILLS_RUNTIME".ToLowerInvariant()

  function Show-Path([string]$Path) {
    if ($Path.StartsWith($HOME + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { return '~' + $Path.Substring($HOME.Length) }
    return $Path
  }

  # A release file, from the folder or downloaded. Retries cover GitHub's occasional 5xx
  # on release downloads; a 4xx won't change, so it fails at once.
  function Get-File([string]$Name, [string]$Path) {
    if ($local) {
      $src = Join-Path $base $Name
      if (-not (Test-Path -LiteralPath $src)) { throw "failed: $Name isn't in $(Show-Path $base)" }
      Copy-Item -LiteralPath $src -Destination $Path
      return
    }
    $Url = "$base/$Name"
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

  # Swap a finished folder in. The old one is renamed aside first, so a folder that's in
  # use (a session running a skill's script) stops the install with nothing changed.
  # Directory.Move is one rename that happens whole or not at all; Move-Item in 5.1 falls
  # back to moving a folder's files one by one when the rename fails.
  function Set-Folder([string]$New, [string]$Target) {
    if (-not (Test-Path -LiteralPath $Target)) { [IO.Directory]::Move($New, $Target); return 'installed' }
    $old = Join-Path (Split-Path -Parent $Target) ('.killer-skills-old-' + [guid]::NewGuid().ToString('N'))
    try { [IO.Directory]::Move($Target, $old) }
    catch { throw "failed: $(Show-Path $Target) is in use, probably by a Claude Code session running one of its scripts. Close it and run the installer again; nothing was changed there." }
    [IO.Directory]::Move($New, $Target)
    Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
    return 'updated'
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

  # The node a skill would run, if it's Node 20 or newer (Playwright's minimum): its version, or $null.
  function Get-Node {
    $ErrorActionPreference = 'SilentlyContinue'
    $c = Get-Command node -CommandType Application | Select-Object -First 1
    if (-not $c) { return $null }
    $out = & $c.Source --version 2>$null
    $code = $LASTEXITCODE
    $v = @($out)[0]
    if ($code -eq 0 -and "$v" -match '^v(\d+)\.\d+\.\d+$' -and [int]$Matches[1] -ge 20) { return "$v" }
    return $null
  }

  # The first Python 3.10 or newer that the Python skills would try (py, python3, python): its version, or $null.
  # The Microsoft Store's stand-in python.exe fails this test, as it should.
  function Get-Python {
    $ErrorActionPreference = 'SilentlyContinue'
    foreach ($try in @(@('py', '-3'), @('python3'), @('python'))) {
      $c = Get-Command $try[0] -CommandType Application | Select-Object -First 1
      if (-not $c) { continue }
      $rest = @($try | Select-Object -Skip 1)
      $out = & $c.Source @rest -c 'import sys; print(sys.version.split()[0])' 2>$null
      $code = $LASTEXITCODE
      $v = @($out)[0]
      if ($code -eq 0 -and "$v" -match '^3\.(\d+)\.\d+' -and [int]$Matches[1] -ge 10) { return "Python $v" }
    }
    return $null
  }

  $packs = @{
    runtime = @{ Asset = 'killer-skills-runtime-windows-x64.zip'; Folder = 'killer-skills'; What = 'the runtime'; Manifest = 'runtime.json'
      Describe = { param($i) "Playwright $($i.playwright), Chrome $($i.chrome) and Mermaid $($i.mermaid)" } }
    fonts = @{ Asset = 'killer-skills-fonts.zip'; Folder = 'killer-skills-fonts'; What = 'the font pack'; Manifest = 'fonts.json'
      Describe = { param($i) "$($i.count) families" } }
    node = @{ Asset = 'killer-skills-node-windows-x64.zip'; Folder = 'killer-skills-node'; What = 'Node.js'; Manifest = 'node.json'
      Describe = { param($i) "v$($i.version)" } }
    python = @{ Asset = 'killer-skills-python-windows-x64.zip'; Folder = 'killer-skills-python'; What = 'Python'; Manifest = 'python.json'
      Describe = { param($i) "$($i.version)" } }
  }
  $staging = New-Object System.Collections.Generic.List[string]

  # A pack into its folder in the cache, unless the same one is already there.
  function Install-Pack($Pack) {
    $target = Join-Path $caches $Pack.Folder
    $marker = Join-Path $target 'installed-sha256.txt'
    $want = $sums[$Pack.Asset]
    if ((Test-Path -LiteralPath $marker) -and ((Get-Content -Raw -LiteralPath $marker).Trim() -eq $want)) {
      Write-Host "$($Pack.What) already in $(Show-Path $target)"
      return
    }
    if ($local -and -not (Test-Path -LiteralPath (Join-Path $base $Pack.Asset))) {
      Write-Host "skipped $($Pack.What): $($Pack.Asset) isn't in $(Show-Path $base)"
      return
    }
    if (-not $local) { Write-Host "downloading $($Pack.What) ..." }
    $file = Join-Path $tmp $Pack.Asset
    Get-File $Pack.Asset $file
    if ((Get-Sha256 $file) -ne $want) { throw "failed: $($Pack.Asset) doesn't match SHA256SUMS.txt; $($Pack.What) wasn't changed" }
    New-Item -ItemType Directory -Force -Path $caches | Out-Null
    # Unpack beside the target, so the swap is a rename on the same drive.
    $stage = Join-Path $caches (".$($Pack.Folder)-" + [guid]::NewGuid().ToString('N'))
    $staging.Add($stage)
    [IO.Compression.ZipFile]::ExtractToDirectory($file, $stage)
    if (-not (Test-Path -LiteralPath (Join-Path $stage $Pack.Manifest))) { throw "failed: $($Pack.Asset) has no $($Pack.Manifest)" }
    Set-Content -LiteralPath (Join-Path $stage 'installed-sha256.txt') -Value $want -Encoding ascii
    $verb = Set-Folder $stage $target
    $info = Get-Content -Raw -LiteralPath (Join-Path $target $Pack.Manifest) | ConvertFrom-Json
    Write-Host "$verb $($Pack.What): $(& $Pack.Describe $info), in $(Show-Path $target)"
  }

  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('killer-skills-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmp | Out-Null
  try {
    if ($local) { Write-Host "installing from $(Show-Path $base)" }
    $sumsFile = Join-Path $tmp 'SHA256SUMS.txt'
    Get-File 'SHA256SUMS.txt' $sumsFile
    $sums = @{}
    foreach ($line in Get-Content $sumsFile) {
      if ($line -match '^([0-9a-f]{64})  (.+)$') { $sums[$Matches[2]] = $Matches[1] }
    }
    $available = @($sums.Keys | Where-Object { $_ -like '*.zip' } | ForEach-Object { $_ -replace '\.zip$', '' } |
      Where-Object { $_ -ne $bundle -and $_ -notlike "$bundle-*" } | Sort-Object)
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
    $staging.Add($stage)
    New-Item -ItemType Directory -Path $stage | Out-Null
    $count = 0
    foreach ($name in $names) {
      $zip = Join-Path $tmp "$name.zip"
      Get-File "$name.zip" $zip
      if ((Get-Sha256 $zip) -ne $sums["$name.zip"]) { throw "failed: $name.zip doesn't match SHA256SUMS.txt; nothing was changed for $name" }
      $x = Join-Path $stage $name
      [IO.Compression.ZipFile]::ExtractToDirectory($zip, $x)
      if (-not (Test-Path -LiteralPath (Join-Path (Join-Path $x $name) 'SKILL.md'))) { throw "failed: $name.zip has no $name/SKILL.md" }
      $verb = Set-Folder (Join-Path $x $name) (Join-Path $dest $name)
      Write-Host "$verb $name"
      $count++
    }

    $onWindows = ($PSVersionTable.PSVersion.Major -lt 6) -or $IsWindows
    $browser = @($names | Where-Object { @('killer-design', 'killer-repo', 'killer-slides') -contains $_ }).Count -gt 0   # the skills that drive Chromium
    $pythonSkills = @('killer-council', 'killer-bugs', 'killer-tests', 'killer-docs', 'killer-perf', 'killer-release', 'killer-upgrade', 'killer-goal')
    $council = @($names | Where-Object { $pythonSkills -contains $_ }).Count -gt 0   # the skills that run Python
    if ($mode -ne 'skip') {
      if ($browser -and $onWindows -and $sums.ContainsKey($packs.runtime.Asset)) {
        $design = Join-Path $caches 'killer-design'
        $existing = if ($mode -eq 'all') { $null } else { Get-PlaywrightReady $design }
        if ($existing -and -not (Test-Path -LiteralPath (Join-Path $caches $packs.runtime.Folder))) {
          Write-Host "skipped the runtime: Playwright $existing and its Chromium are already in $(Show-Path $design)"
        } else { Install-Pack $packs.runtime }
      }
      if ($browser -and $onWindows -and $sums.ContainsKey($packs.node.Asset)) {
        $node = if ($mode -eq 'all') { $null } else { Get-Node }
        if ($node -and -not (Test-Path -LiteralPath (Join-Path $caches $packs.node.Folder))) {
          Write-Host "skipped Node.js: $node is on the PATH"
        } else { Install-Pack $packs.node }
      }
      if ($council -and $onWindows -and $sums.ContainsKey($packs.python.Asset)) {
        $python = if ($mode -eq 'all') { $null } else { Get-Python }
        if ($python -and -not (Test-Path -LiteralPath (Join-Path $caches $packs.python.Folder))) {
          Write-Host "skipped Python: $python is on the PATH"
        } else { Install-Pack $packs.python }
      }
      if ($browser -and $sums.ContainsKey($packs.fonts.Asset)) { Install-Pack $packs.fonts }
    }

    $shown = Show-Path $dest
    if ($count -eq 1) { $noun = 'skill'; $them = 'it' } else { $noun = 'skills'; $them = 'them' }
    Write-Host "$count $noun in $shown"
    Write-Host "open a new Claude Code session to use $them"
  }
  finally {
    foreach ($s in $staging) { Remove-Item -LiteralPath $s -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
}

Install-KillerSkills -Skill $Skill -From $PSScriptRoot
