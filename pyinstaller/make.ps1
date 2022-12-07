# Make sure we ran one of these:
#   `poetry install`
#   `poetry install --without dev` (for building with pyinstaller and pytest)
#   `poetry install --without dev,test` (for building with pyinstaller)
# 
# Might need to disable desktop cloud sync. engines during this!
# 
param($ExeName = "hpcflow", $LogLevel = "INFO", $BuildType = 'onefile')
poetry run pyinstaller --log-level=$LogLevel --distpath ./dist/$BuildType --$BuildType --clean -y --name=$ExeName ..\hpcflow\cli\cli.py
If ($BuildType eq 'onedir') {
	Compress-Archive -Path ./dist/$BuildType/$ExeName -DestinationPath ./dist/$BuildType/$ExeName.zip
}
