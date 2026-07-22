from pathlib import Path

# List of files containing nested paths
file_list = """backend/src/return_platform/data_platform/graph/readback.py
backend/src/return_platform/data_platform/graph/sandbox.py
backend/src/return_platform/data_platform/graph/sandbox_runner.py
backend/tests/test_customer_neo4j_readback.py
backend/tests/test_customer_graph_sandbox.py
backend/tests/fixtures/customer_graph_sandbox/customer_p100.json""".split("\n")

for file_path_str in file_list:
    # Convert string to a Path object
    file_path = Path(file_path_str)

    # 1. Automatically create all missing parent folders
    # parents=True creates nested folders; exist_ok=True prevents errors if they exist
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # 2. Safely create the file without overwriting if it already exists
    try:
        with open(file_path, "x") as file:
            file.write("")
        print(f"Created folders and file: {file_path}")
    except FileExistsError:
        print(f"Skipped (File already exists): {file_path}")
