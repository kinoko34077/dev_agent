@echo off
setlocal
set "DEV_AGENT_ROOT=%~dp0"
pushd "%DEV_AGENT_ROOT%"
if errorlevel 1 (
    echo Could not enter the dev_agent repository root: "%DEV_AGENT_ROOT%" 1>&2
    exit /b 1
)

python -m scripts.devfarm_runtime_coordinator serve %*
set "DEV_AGENT_EXIT=%ERRORLEVEL%"
popd
exit /b %DEV_AGENT_EXIT%
