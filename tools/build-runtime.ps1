# Build the Windows runtime for a release: Playwright, the Chromium headless shell it
# launches, and Mermaid's browser build, in one zip. install.ps1 unpacks it into
# ~/.cache/killer-skills, where killer-design and killer-repo look before downloading
# anything.
#
#   .\tools\build-runtime.ps1 [-Out dist]
#
# Versions come from tools/runtime.json. Needs Node.js, npm and the network. Before
# zipping, it launches the bundled Chromium from the runtime folder alone, so a zip
# that can't open a page is never written.
param([string]$Out = 'dist')

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$pins = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'runtime.json') | ConvertFrom-Json
$name = 'killer-skills-runtime-windows-x64.zip'
$work = Join-Path ([IO.Path]::GetTempPath()) ('killer-skills-runtime-' + [guid]::NewGuid().ToString('N'))
$rt = Join-Path $work 'killer-skills'
New-Item -ItemType Directory -Force -Path $rt | Out-Null
$before = $env:PLAYWRIGHT_BROWSERS_PATH

function Invoke-Native([string]$What, [scriptblock]$Run) {
  & $Run
  if ($LASTEXITCODE) { throw "$What failed (exit $LASTEXITCODE)" }
}

try {
  Set-Content -LiteralPath (Join-Path $rt 'package.json') -Value '{ "name": "killer-skills-runtime", "private": true }' -Encoding ascii
  Write-Host "== Playwright $($pins.playwright)"
  Invoke-Native 'npm install playwright' { npm install --prefix $rt --no-audit --no-fund --no-package-lock --loglevel=error "playwright@$($pins.playwright)" }

  Write-Host '== Chromium headless shell'
  $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $rt 'ms-playwright'
  Invoke-Native 'playwright install' { node (Join-Path $rt 'node_modules\playwright\cli.js') install --only-shell chromium }

  Write-Host "== Mermaid $($pins.mermaid)"
  Push-Location $work
  try {
    $tgz = (npm pack --silent "mermaid@$($pins.mermaid)" | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -or -not $tgz) { throw 'npm pack mermaid failed' }
    Invoke-Native 'tar' { tar -xzf $tgz package/dist/mermaid.min.js package/LICENSE }
  } finally { Pop-Location }
  $m = New-Item -ItemType Directory -Path (Join-Path $rt 'mermaid')
  Move-Item -LiteralPath (Join-Path $work 'package\dist\mermaid.min.js') -Destination $m.FullName
  Move-Item -LiteralPath (Join-Path $work 'package\LICENSE') -Destination (Join-Path $m.FullName 'LICENSE.txt')

  $pw = (Get-Content -Raw -LiteralPath (Join-Path $rt 'node_modules\playwright\package.json') | ConvertFrom-Json).version
  $shell = (Get-Content -Raw -LiteralPath (Join-Path $rt 'node_modules\playwright-core\browsers.json') | ConvertFrom-Json).browsers |
    Where-Object { $_.name -eq 'chromium-headless-shell' } | Select-Object -First 1
  if (-not (Test-Path -LiteralPath (Join-Path $env:PLAYWRIGHT_BROWSERS_PATH "chromium_headless_shell-$($shell.revision)\INSTALLATION_COMPLETE"))) {
    throw "Chromium headless shell $($shell.revision) isn't in the runtime"
  }
  $info = [ordered]@{
    platform = 'windows-x64'
    playwright = $pw
    chromium_headless_shell = $shell.revision
    chrome = $shell.browserVersion
    mermaid = $pins.mermaid
  }
  Set-Content -LiteralPath (Join-Path $rt 'runtime.json') -Value ($info | ConvertTo-Json) -Encoding ascii

  Write-Host '== Smoke test: open a page with the runtime alone'
  $env:KS_RUNTIME = $rt
  $smoke = @'
const path = require('path');
const { createRequire } = require('module');
const rt = process.env.KS_RUNTIME;
process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(rt, 'ms-playwright');
const { chromium } = createRequire(path.join(rt, 'x.js'))('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setContent('<h1>ok</h1>');
  const text = await page.textContent('h1');
  const version = browser.version();
  await browser.close();
  if (text !== 'ok') throw new Error('the page did not render');
  console.log('opened a page in Chromium ' + version);
})().catch((e) => { console.error(e); process.exit(1); });
'@
  Invoke-Native 'smoke test' { node -e $smoke }

  New-Item -ItemType Directory -Force -Path $Out | Out-Null
  $zip = Join-Path (Resolve-Path $Out).Path $name
  if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [IO.Compression.ZipFile]::CreateFromDirectory($rt, $zip, [IO.Compression.CompressionLevel]::Optimal, $false)
  $mb = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
  Write-Host "wrote $zip ($mb MB): Playwright $pw, Chrome headless shell $($shell.browserVersion), Mermaid $($pins.mermaid)"
}
finally {
  $env:PLAYWRIGHT_BROWSERS_PATH = $before
  Remove-Item Env:KS_RUNTIME -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
