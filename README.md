# Automatic-Desktop-Cleaner
Automatic Desktop Cleaner Overview The Automatic Desktop Cleaner is a script that helps you organize and clean your desktop by moving files to specified folders based on certain criteria. This can help keep your desktop tidy and make it easier to find files.
# Features
. File Organization: Automatically move files from the desktop to designated folders.
. Customization: Users can customize rules for file organization.
. Logging: Keep a log of moved files for reference.

# Prerequisites
Python installed on your system.
Installation
Clone the repository:

git clone https://github.com/Joeltabe/Automatic-Desktop-Cleaner.git .

Change to the project directory:

cd desktop-cleaner
Install dependencies:

pip install -r requirements.txt
Usage
Edit the config.json file to set up your rules for file organization.

  {
    "rules": [
      {
        "file_type": "txt",
        "destination_folder": "Documents"
      },
      {
        "file_type": "jpg",
        "destination_folder": "Images"
      }
      // Add more rules as needed
    ]
  }
Run the script:

python desktop_cleaner.py
# Configuration
Rules: Define rules in the config.json file. Each rule consists of a file type and the destination folder.
Example
Suppose you have a desktop with files report.txt and image.jpg. With the provided configuration, running the script will move report.txt to the Documents folder and image.jpg to the Images folder.

#Logging
A log file (desktop_cleaner.log) is generated in the project directory, providing information about the files moved.
Contributions
Contributions are welcome! If you have any ideas for improvement or encounter issues, feel free to open an issue or submit a pull request.

# License
This project is licensed under the MIT License.

# Acknowledgments
This project was inspired by the need for desktop organization.
