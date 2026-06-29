#!/usr/bin/env python3
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

# Color helpers
def print_green(msg):
    print(f"\033[92m{msg}\033[0m")

def print_yellow(msg):
    print(f"\033[93m{msg}\033[0m")

def print_cyan(msg):
    print(f"\033[96m{msg}\033[0m")

def print_red(msg):
    print(f"\033[91m{msg}\033[0m")

def check_prerequisites():
    print_cyan("Checking prerequisites...")
    # Check Python version (requires 3.9+ for Anki support)
    if sys.version_info < (3, 9):
        print_red(f"Error: Python 3.9 or higher is required. You are using {sys.version.split()[0]}.")
        sys.exit(1)
    
    # Check if git is installed
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print_red("Error: Git is not installed or not in your PATH.")
        sys.exit(1)
    
    print_green("Prerequisites met!")

def initialize_submodules():
    print_cyan("\n--- Step 1: Initializing Git Submodules ---")
    poke_engine_dir = Path("src/Ankimon/poke_engine")
    
    # Check if submodules are checked out
    if not (poke_engine_dir / "README.md").exists() and not (poke_engine_dir / "poke_engine").exists():
        print_yellow("Submodules (poke-engine) are not initialized. Initializing now...")
        try:
            subprocess.run(["git", "submodule", "update", "--init", "--recursive"], check=True)
            print_green("Submodules initialized successfully.")
        except subprocess.CalledProcessError as e:
            print_red(f"Error initializing submodules: {e}")
            sys.exit(1)
    else:
        print_green("Submodules are already initialized.")

def setup_virtual_environment():
    print_cyan("\n--- Step 2: Setting up Virtual Environment ---")
    venv_dir = Path("venv")
    
    # Determine the python/pip paths inside the venv
    if sys.platform == "win32":
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python"

    # If the venv exists but is broken or incompatible with the current platform, recreate it
    is_broken = False
    if venv_dir.exists():
        if not venv_python.exists():
            print_yellow(f"Virtual environment python executable not found at '{venv_python}' (possibly created on a different OS).")
            is_broken = True
        else:
            try:
                subprocess.run([str(venv_python), "--version"], capture_output=True, check=True)
            except Exception:
                print_yellow("Virtual environment python executable is not runnable on this system.")
                is_broken = True

    if is_broken:
        print_yellow("Re-creating virtual environment...")
        try:
            if venv_dir.is_symlink():
                venv_dir.unlink()
            else:
                shutil.rmtree(venv_dir)
        except Exception as e:
            print_red(f"Failed to remove broken venv directory: {e}")
            sys.exit(1)
            
    if not venv_dir.exists():
        print_yellow("Creating virtual environment at 'venv'...")
        try:
            subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
            print_green("Virtual environment created.")
        except subprocess.CalledProcessError as e:
            print_red(f"Error creating virtual environment: {e}")
            sys.exit(1)
    else:
        print_green("Virtual environment already exists and is healthy.")
    
    print_cyan("Upgrading pip inside virtual environment...")
    try:
        subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    except subprocess.CalledProcessError as e:
        print_yellow(f"Warning: Failed to upgrade pip: {e}")

    print_cyan("Installing dependencies from requirements.txt...")
    if Path("requirements.txt").exists():
        try:
            subprocess.run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"], check=True)
            print_green("Dependencies installed successfully!")
        except subprocess.CalledProcessError as e:
            print_red(f"Error installing dependencies: {e}")
            sys.exit(1)
    else:
        print_yellow("requirements.txt not found. Skipping dependency installation.")

def detect_anki_addons_dir():
    print_cyan("\n--- Step 3: Detecting Anki Addons Directory ---")
    addons_dir = None
    
    # Common locations depending on platform
    if sys.platform == "win32":
        possible_dirs = [
            os.path.expandvars(r"%APPDATA%\Anki2\addons21"),
            os.path.expandvars(r"%LOCALAPPDATA%\Anki2\addons21"),
        ]
    elif sys.platform == "darwin":  # macOS
        possible_dirs = [
            os.path.expanduser("~/Library/Application Support/Anki2/addons21")
        ]
    else:  # Linux / Unix
        possible_dirs = [
            os.path.expanduser("~/.local/share/Anki2/addons21"),
            os.path.expanduser("~/.var/app/net.ankiweb.Anki/data/Anki2/addons21")  # Flatpak
        ]
    
    for directory in possible_dirs:
        path = Path(directory)
        if path.exists() and path.is_dir():
            addons_dir = path
            break
            
    if addons_dir:
        print_green(f"Detected Anki addons directory: {addons_dir}")
        use_detected = input("Use this detected directory? [Y/n]: ").strip().lower()
        if use_detected not in ("", "y", "yes"):
            addons_dir = None
            
    if not addons_dir:
        while True:
            custom_path = input("Please enter the absolute path to your Anki 'addons21' directory: ").strip()
            if not custom_path:
                print_red("Path cannot be empty.")
                continue
            path = Path(os.path.expandvars(os.path.expanduser(custom_path)))
            if path.exists() and path.is_dir():
                addons_dir = path
                break
            else:
                print_red(f"Error: Directory '{path}' does not exist. Please try again.")
                
    return addons_dir

def create_addon_symlink(addons_dir: Path):
    print_cyan("\n--- Step 4: Symlinking Addon to Anki ---")
    addon_folder_name = "1908235722"  # Ankimon addon folder name
    target_link = addons_dir / addon_folder_name
    source_dir = Path("src/Ankimon").resolve()
    
    if not source_dir.exists():
        print_red(f"Error: Source directory '{source_dir}' does not exist.")
        sys.exit(1)
        
    # Safely clean up existing target link/directory
    if target_link.exists() or target_link.is_symlink():
        print_yellow(f"An existing addon folder or link was found at: {target_link}")
        confirm = input("Are you sure you want to replace it? (Existing files in that folder will be removed) [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print_yellow("Skipping symlink creation.")
            return
            
        print_cyan("Removing existing file/link...")
        if target_link.is_symlink():
            target_link.unlink()
        elif target_link.is_dir():
            # Try removing as junction/symlink on Windows, or remove normally
            try:
                os.rmdir(target_link)
            except OSError:
                shutil.rmtree(target_link)
        else:
            target_link.unlink()
                
    # Create the link
    print_cyan(f"Linking {source_dir} -> {target_link}")
    try:
        if sys.platform == "win32":
            # On Windows, use directory junction to avoid needing administrator privileges
            subprocess.run(["cmd", "/c", "mklink", "/j", str(target_link), str(source_dir)], check=True)
        else:
            # On Unix, use standard symbolic link
            os.symlink(source_dir, target_link)
        print_green("Symlink created successfully.")
    except Exception as e:
        print_red(f"Failed to create symlink: {e}")
        print_yellow("You may need to create the link manually or run this script with elevated privileges.")

def configure_vscode_settings(addons_dir: Path):
    print_cyan("\n--- Step 5: Configuring VS Code Settings ---")
    vscode_dir = Path(".vscode")
    vscode_dir.mkdir(exist_ok=True)
    
    settings_file = vscode_dir / "settings.json"
    launch_file = vscode_dir / "launch.json"
    
    # Paths for interpreter
    if sys.platform == "win32":
        venv_python_path = "${workspaceFolder}/venv/Scripts/python.exe"
        venv_python_abs = str(Path("venv/Scripts/python.exe").resolve())
        venv_anki_path = str(Path("venv/Scripts/anki.exe").resolve())
    else:
        venv_python_path = "${workspaceFolder}/venv/bin/python"
        venv_python_abs = str(Path("venv/bin/python").resolve())
        venv_anki_path = str(Path("venv/bin/anki").resolve())
        
    anki_base_dir = str(addons_dir.parent.resolve())
    
    # 1. settings.json configuration
    settings_data = {}
    if settings_file.exists():
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings_data = json.load(f)
        except json.JSONDecodeError:
            print_yellow("Warning: Existing settings.json is not valid JSON. Overwriting it.")
            
    # Update settings
    settings_data["python.defaultInterpreterPath"] = venv_python_abs
    
    extra_paths = settings_data.get("python.analysis.extraPaths", [])
    if "./src" not in extra_paths:
        extra_paths.append("./src")
    settings_data["python.analysis.extraPaths"] = extra_paths
    
    settings_data["python.testing.pytestEnabled"] = True
    settings_data["python.testing.pytestArgs"] = ["tests"]
    settings_data["python.testing.unittestEnabled"] = False
    settings_data["editor.formatOnSave"] = True
    
    # Python specific formatter (Ruff)
    python_block = settings_data.get("[python]", {})
    python_block["editor.defaultFormatter"] = "charliermarsh.ruff"
    python_block["editor.codeActionsOnSave"] = {
        "source.organizeImports": "always",
        "source.fixAll": "always"
    }
    settings_data["[python]"] = python_block
    
    # Save settings.json
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(settings_data, f, indent=4)
    print_green("VS Code settings.json configured.")
    
    # 2. launch.json configuration
    launch_data = {"version": "0.2.0", "configurations": []}
    if launch_file.exists():
        try:
            with open(launch_file, "r", encoding="utf-8") as f:
                launch_data = json.load(f)
        except json.JSONDecodeError:
            print_yellow("Warning: Existing launch.json is not valid JSON. Overwriting it.")
            
    # Check if "Python Anki" launch configuration already exists
    has_anki_config = False
    for config in launch_data.get("configurations", []):
        if config.get("name") == "Python Anki":
            # Update paths
            config["program"] = venv_anki_path
            config["python"] = venv_python_abs
            config["args"] = ["-b", anki_base_dir]
            has_anki_config = True
            break
            
    if not has_anki_config:
        anki_config = {
            "name": "Python Anki",
            "type": "debugpy",
            "request": "launch",
            "stopOnEntry": False,
            "program": venv_anki_path,
            "cwd": "${workspaceRoot}",
            "python": venv_python_abs,
            "env": {},
            "args": [
                "-b",
                anki_base_dir
            ],
            "envFile": "${workspaceRoot}/.env"
        }
        launch_data["configurations"].append(anki_config)
        
    # Save launch.json
    with open(launch_file, "w", encoding="utf-8") as f:
        json.dump(launch_data, f, indent=4)
    print_green("VS Code launch.json configured.")

def main():
    print_green("=========================================================")
    print_green("          Ankimon Development Environment Setup          ")
    print_green("=========================================================")
    
    check_prerequisites()
    initialize_submodules()
    setup_virtual_environment()
    
    addons_dir = detect_anki_addons_dir()
    if addons_dir:
        create_addon_symlink(addons_dir)
        configure_vscode_settings(addons_dir)
        
    print_green("\n=========================================================")
    print_green("  Setup Complete! Your development environment is ready. ")
    print_green("=========================================================")
    print_cyan("Summary of accomplishments:")
    print_cyan("  1. Checked requirements & initialized submodules recursively.")
    print_cyan("  2. Created Python virtual environment and installed dependencies.")
    print_cyan("  3. Symlinked/Junctioned Ankimon addon source into your local Anki.")
    print_cyan("  4. Configured VS Code settings & interpreter path.")
    print_cyan("  5. Created/updated VS Code 'Python Anki' debugger launch profile.")
    print_cyan("\nYou can now open this repository in VS Code and press F5 to debug Anki with Ankimon loaded!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print_red("\nSetup aborted by user.")
        sys.exit(1)
