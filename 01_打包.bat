@echo off
@chcp 65001 > nul

    call conda create --name KeywordGacha python=3.12 --yes
    call conda activate KeywordGacha

    rd /s /q dist
    rd /s /q build
    del /q KeywordGacha.spec

    call pip install -r requirements.txt
    call pyinstaller --name KeywordGacha --clean --noconfirm main.py

    xcopy "prompt" "dist\KeywordGacha\prompt" /E /I /H /Y
    copy /y "config.json" "dist\KeywordGacha\"
    copy /y "blacklist.txt" "dist\KeywordGacha\"

    call conda deactivate
    call conda env remove --name KeywordGacha --yes