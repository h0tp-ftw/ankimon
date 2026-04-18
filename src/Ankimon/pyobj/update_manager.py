import os
import shutil
import urllib.request
import urllib.error
import zipfile
import json
import subprocess
from pathlib import Path
from aqt import mw
from ..resources import addon_dir, pre_update_backup_path, update_temp_path

class UpdateManager:
    def __init__(self, logger):
        self.logger = logger
        self.github_repo = "h0tp-ftw/ankimon"
        self.api_base = f"https://api.github.com/repos/{self.github_repo}"
        
        # Files/folders to explicitly not overwrite during update!
        self.preserve_paths = ["user_files"]
        # .gitignore handles others but we want to ensure we don't accidentally nuke local user data

    def check_for_git(self):
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def create_pre_update_backup(self):
        """Creates a complete backup of the Ankimon directory before an update."""
        try:
            if pre_update_backup_path.exists():
                shutil.rmtree(pre_update_backup_path)
            shutil.copytree(addon_dir, pre_update_backup_path)
            self.logger.log("game", f"Pre-update backup created at {pre_update_backup_path}")
            return True
        except Exception as e:
            self.logger.log("game", f"Failed to create pre-update backup: {e}")
            return False

    def get_current_version_info(self):
        from ..resources import addon_ver
        if self.check_for_git():
            try:
                # Check if we are inside a git work tree
                is_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=addon_dir, capture_output=True, text=True, check=True).stdout.strip()
                if is_git == 'true':
                    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=addon_dir, capture_output=True, text=True).stdout.strip()
                    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=addon_dir, capture_output=True, text=True).stdout.strip()
                    return f"Git: {branch} ({commit})"
            except subprocess.CalledProcessError:
                pass
        return f"Standard: {addon_ver}"

    def get_latest_experimental_tag(self):
        """Queries GitHub for the latest tag."""
        tags = self.fetch_github_tags()
        if tags:
            return tags[0]
        return None

    def fetch_github_tags(self):
        try:
            url = f"{self.api_base}/releases"
            req = urllib.request.Request(url, headers={'User-Agent': 'Ankimon-Updater'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                # Releases are sorted by creation date descending automatically
                tags = [d['tag_name'] for d in data if 'tag_name' in d]
                
                # fallback if they dont use releases might just be tags
                if not tags:
                    url2 = f"{self.api_base}/tags"
                    req2 = urllib.request.Request(url2, headers={'User-Agent': 'Ankimon-Updater'})
                    with urllib.request.urlopen(req2) as resp2:
                        tags_data = json.loads(resp2.read().decode())
                        tags = [t['name'] for t in tags_data]
                return tags
        except Exception as e:
            self.logger.log("game", f"Failed to fetch tags: {e}")
            return []

    def fetch_github_branches(self):
        try:
            url = f"{self.api_base}/branches"
            req = urllib.request.Request(url, headers={'User-Agent': 'Ankimon-Updater'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                return [d['name'] for d in data]
        except Exception as e:
            self.logger.log("game", f"Failed to fetch branches: {e}")
            return []
            
    def fetch_github_prs(self):
        try:
            url = f"{self.api_base}/pulls?state=open"
            req = urllib.request.Request(url, headers={'User-Agent': 'Ankimon-Updater'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                return [f"{d['number']}: {d['title']}" for d in data]
        except Exception as e:
            self.logger.log("game", f"Failed to fetch PRs: {e}")
            return []

    def fetch_standard_update(self, tag_name=None):
        """Downloads and extracts the source zip from GitHub."""
        if not tag_name:
            tag_name = self.get_latest_experimental_tag()
        if not tag_name:
            return False, "Failed to determine latest version."

        zip_url = f"https://github.com/{self.github_repo}/archive/refs/tags/{tag_name}.zip"
        
        try:
            # Prepare temp folder
            if update_temp_path.exists():
                shutil.rmtree(update_temp_path)
            update_temp_path.mkdir(parents=True)
            
            zip_path = update_temp_path / "update.zip"
            
            self.logger.log("game", f"Downloading {zip_url} to {zip_path}...")
            urllib.request.urlretrieve(zip_url, zip_path)
            
            self.logger.log("game", "Extracting ZIP...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(update_temp_path)
                
            # The structure in the ZIP is usually: repo-name-tag_name/src/Ankimon
            extracted_folder = None
            for item in update_temp_path.iterdir():
                if item.is_dir() and item.name.startswith("ankimon-"):
                    extracted_folder = item / "src" / "Ankimon"
                    break
                    
            if not extracted_folder or not extracted_folder.exists():
                return False, "Could not find valid Ankimon folder in downloaded ZIP."
                
            return True, extracted_folder
            
        except Exception as e:
            return False, f"Standard update fetch failed: {e}"

    def fetch_git_update(self, source_type, source_value):
        """Uses git to clone/fetch the specfic ref."""
        try:
            if update_temp_path.exists():
                shutil.rmtree(update_temp_path)
            
            # Simple approach: clone the entire repo, then checkout the ref
            clone_cmd = ["git", "clone", f"https://github.com/{self.github_repo}.git", str(update_temp_path)]
            subprocess.run(clone_cmd, capture_output=True, check=True)
            
            cwd = str(update_temp_path)
            
            if source_type == "PR":
                # Extract number if it's "123: title"
                pr_val = source_value.split(":")[0].strip() if ":" in source_value else source_value
                
                # Fetch the PR ref
                fetch_cmd = ["git", "fetch", "origin", f"pull/{pr_val}/head:pr-{pr_val}"]
                subprocess.run(fetch_cmd, capture_output=True, check=True, cwd=cwd)
                checkout_cmd = ["git", "checkout", f"pr-{pr_val}"]
                subprocess.run(checkout_cmd, capture_output=True, check=True, cwd=cwd)
                
            elif source_type in ["BRANCH", "TAG", "HASH", "MAIN"]:
                # Checkout the specified ref
                val = "main" if source_type == "MAIN" else source_value
                checkout_cmd = ["git", "checkout", val]
                subprocess.run(checkout_cmd, capture_output=True, check=True, cwd=cwd)
                
            extracted_folder = update_temp_path / "src" / "Ankimon"
            if not extracted_folder.exists():
                 return False, "Failed to locate src/Ankimon in Git repository."
                 
            return True, extracted_folder
            
        except subprocess.CalledProcessError as e:
            stderr_out = e.stderr.decode() if e.stderr else str(e)
            return False, f"Git command failed: {stderr_out}"
        except Exception as e:
            return False, f"Git update fetch failed: {e}"

    def apply_update(self, new_source_dir):
        """Overwrites local files with new files from new_source_dir, skipping preserved paths."""
        try:
            for item in new_source_dir.rglob('*'):
                # Get the relative path of the item w.r.t the new_source_dir
                rel_path = item.relative_to(new_source_dir)
                target_path = addon_dir / rel_path
                
                # Check if this item is in the preserve list or should be skipped
                skip = False
                for preserve in self.preserve_paths:
                    # if the relative path starts with a preserved path (e.g. user_files/...)
                    if str(rel_path).replace("\\", "/").startswith(preserve):
                        skip = True
                        break
                
                if skip:
                    continue
                    
                if item.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    # Make sure the target directory exists
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target_path)
                    
            return True, "Update applied successfully. Please restart Anki."
        except Exception as e:
            return False, f"Failed to apply update files: {e}"
