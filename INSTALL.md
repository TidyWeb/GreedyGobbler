# Install and start Greedy Gobbler

Greedy Gobbler runs as a local web app in your browser. First download and open the project folder; then choose Docker or Python. This project does not include a one-click installer.

## Get the project folder

1. Open the public [Greedy Gobbler repository on GitHub](https://github.com/TidyWeb/GreedyGobbler). A GitHub account is not needed to download a public project.
2. Above the file list, choose **Code**, then **Download ZIP**. GitHub's [ZIP download instructions](https://docs.github.com/en/repositories/working-with-files/using-files/downloading-source-code-archives) show the same steps.
3. Find the ZIP in your Downloads folder and extract or unzip it. Do not try to run the app from the ZIP preview.
4. Move the extracted folder somewhere you plan to keep it, for example `Documents/Apps/Greedy Gobbler`. The folder name can vary, often appearing as `GreedyGobbler-main`.
5. Open the extracted folder, then open the project folder inside it if needed. Stop when you can see `app.py`, `README.md`, `requirements.txt` and `docker-compose.yml`. This is the folder where you will run the commands.
6. Open a terminal or command window with this project folder as its current folder. If your file manager offers **Open in Terminal**, you can use that. The app is not started by double-clicking a project file.

## Choose how to run it

- **Docker:** use the [Docker instructions](README.md#running-with-docker) if Docker with Compose is installed. Docker supplies the Python app dependencies and the system tools used for OCR. The Docker route is available on Windows, macOS and Linux; on Windows, use Docker Desktop and its PowerShell or terminal. [Install Docker](https://docs.docker.com/get-started/get-docker/).
- **Python without Docker:** use the [Python instructions](README.md#running-without-docker) from a Linux or macOS terminal. This needs Python 3.11 or newer. Scanned-PDF and image OCR needs Poppler and Tesseract installed separately. Native Windows Python commands are not covered here.

Both routes open the app at [http://127.0.0.1:5001](http://127.0.0.1:5001). Leave the terminal running while you use it; press **Ctrl+C** in that terminal to stop it. Follow the matching README section to start it again.
