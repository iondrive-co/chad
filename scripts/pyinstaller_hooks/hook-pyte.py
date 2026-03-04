from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("pyte")
hiddenimports = collect_submodules("pyte")
