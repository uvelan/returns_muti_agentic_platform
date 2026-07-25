import glob
for f in glob.glob('frontend/src/dev/adapters/*.ts'):
    with open(f, 'r') as file:
        text = file.read()
    text = text.replace('from \"../ports/', 'from \"../../api/ports/')
    text = text.replace('from \"../ports\"', 'from \"../../api/ports\"')
    text = text.replace('from \'../ports/', 'from \'../../api/ports/')
    text = text.replace('from \'../ports\'', 'from \'../../api/ports\'')
    text = text.replace('from \"./graphExplorerPort\"', 'from \"../../api/adapters/graphExplorerPort\"')
    text = text.replace('from \'./graphExplorerPort\'', 'from \'../../api/adapters/graphExplorerPort\'')
    with open(f, 'w') as file:
        file.write(text)
