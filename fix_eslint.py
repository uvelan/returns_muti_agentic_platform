import glob
import re

files = glob.glob('frontend/src/**/*.tsx', recursive=True) + glob.glob('frontend/src/**/*.ts', recursive=True)

for f in files:
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    orig = content
    # Replace FormEvent with SyntheticEvent
    content = content.replace("FormEvent", "SyntheticEvent")
    
    if orig != content:
        with open(f, 'w', encoding='utf-8') as file:
            file.write(content)
