import os
import shutil
import json
from datetime import datetime

def load_config():
    with open("config.json", "r") as config_file:
        config = json.load(config_file)
    return config.get("rules", [])

def organize_files(rules):
    desktop_path = os.path.expanduser("~/Desktop")
    log_file_path = "desktop_cleaner.log"
    
    # Create log file or append to existing log
    with open(log_file_path, "a") as log_file:
        log_file.write("\n\n" + "=" * 30 + "\n")
        log_file.write(f"Cleaning started at {datetime.now()}\n")

        for rule in rules:
            file_type = rule.get("file_type", "")
            destination_folder = rule.get("destination_folder", "Miscellaneous")
            
            # Create the destination folder if it doesn't exist
            destination_path = os.path.join(desktop_path, destination_folder)
            os.makedirs(destination_path, exist_ok=True)

            # Move files based on the rule
            for filename in os.listdir(desktop_path):
                if filename.endswith(f".{file_type}"):
                    src_path = os.path.join(desktop_path, filename)
                    dest_path = os.path.join(destination_path, filename)
                    
                    shutil.move(src_path, dest_path)
                    
                    # Log the moved file
                    log_file.write(f"Moved: {filename} to {destination_folder}\n")

        log_file.write(f"Cleaning completed at {datetime.now()}\n")

def main():
    rules = load_config()
    organize_files(rules)

if __name__ == "__main__":
    main()
