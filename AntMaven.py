from openai import OpenAI
import subprocess as sp
import os
from flask import Flask, redirect, request, jsonify, render_template, url_for
# from flask_cors import CORS
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

JFROG_BASE_URL = os.getenv("JFROG_BASE_URL")
JFROG_REPO = os.getenv("JFROG_REPO")
JFROG_USERNAME = os.getenv("JFROG_USERNAME")
JFROG_PASSWORD = os.getenv("JFROG_PASSWORD")
app = Flask(__name__)
# CORS(app)
#hi
check_final_maven_content = None

ant_to_maven_prompt_step1 = """
You are an expert in converting Ant build files to Maven build files.
Your task is to read the provided Ant build file end to end and generate the Entire corresponding Maven build file. Ensure it covers all the dependencies, plugins,and other necessary configurations.
Generate the following Maven pom.xml file without adding any surrounding triple quotes or other formatting.
Ant build file content:
{content}
Important: Output should support direct execution.
"""

ant_to_maven_prompt_step2 = """
Below is the initial Maven build file generated from the Ant build file. Enhance the following Maven pom.xml file without adding any surrounding triple quotes or other formatting.
Add the following dependencies:
{dependencies}
Add jackson-databind-2.15.2.jar dependency only  twice, Consider Mockito version 5.5 only as a wrong dependency, and log4j-core-2.20.0.jar as a missing dependency. DO NOT consider the ups-core-1.0.jar, shared-1.0.0.jar for any of the 3 Error.
{maven_content}
Output: Only directly executable Maven pom.xml file which I can automate for my next coding step.
"""

ant_to_maven_prompt_step3 = """ While adding ups-core-1.0.jar, shared-1.0.0.jar and mysql-connector-j-8.0.33.jar dependencies, follow the exact structure, format, syntax:
        <dependency>
            <groupId>com.ups</groupId>
            <artifactId>ups-core</artifactId>
            <version>1.0</version>
            <scope>system</scope>
            <systemPath>$project.basedir/lib/ups-core-1.0.jar</systemPath>
        </dependency>
        <dependency>
            <groupId>com.ups</groupId>
            <artifactId>shared</artifactId>
            <version>1.0</version>
            <scope>system</scope>
            <systemPath>$project.basedir/lib/shared-1.0.0.jar</systemPath>
        </dependency>
        <dependency>
            <groupId>mysql</groupId>
            <artifactId>mysql-connector-java</artifactId>
            <version>8.0.33</version>
            <scope>system</scope>
            <systemPath>$project.basedir/lib/mysql-connector-j-8.0.33.jar</systemPath>
        </dependency>
Add the above dependencies to the initial Maven build file.
Initial Maven build file:
{maven_content}
Output: As mentioned above make sure the dependencies follow the structure and are added to the initial maven build file. Don't give gibberish output as this is part of data pipeline."""

ant_to_maven_prompt_step4 = """
Below is the initial Maven build file generated from the Ant build file. 
Compare the content with the given dependencies and plugins. 
If you find any duplicate dependencies, wrong dependencies, or missing dependencies, give the output as specified below.
These are the actual dependencies and plugins that were given:
{dependencies}
Initial Maven build file:
{maven_content}
Take this as reference: "We found that the generated Maven file currently has some issues related to Duplicate dependencies, Wrong Dependencies, and Missing dependencies. We will take care of these issues and generate the final Maven build file which is directly executable.

1. Duplicate Dependencies:
   - The `hamcrest-core` dependency is defined twice.
   - The `spring-boot-starter-test` dependency is defined twice.

2. Wrong Dependency:
   - The dependency for `spring-boot-starter-nonexistent` is not needed and should be removed.

3. Missing Dependencies:
   - No missing dependencies were identified as the provided list matches with the actual dependencies defined."
Output: We found that the generated Maven file currently has some issues related to Duplicate dependencies, Wrong Dependencies and Missing dependencies. We will take care of the issues and generate the final Maven build file which is directly executable. 
"""


# Set 1: Parsing Ant build file and converting to Maven fragments
def parse_ant_build_file(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    
    project_name = root.attrib.get('name')
    default_target = root.attrib.get('default')
    basedir = root.attrib.get('basedir')
    
    properties = {}
    for prop in root.findall('property'):
        name = prop.attrib.get('name')
        location = prop.attrib.get('location')
        properties[name] = location
    
    targets = {}
    for target in root.findall('target'):
        target_name = target.attrib.get('name')
        depends = target.attrib.get('depends')
        tasks = [parse_element(task) for task in target]
        targets[target_name] = {
            'depends': depends,
            'tasks': tasks,
            'attributes': target.attrib
        }

    parsed_data = {
        'project_name': project_name,
        'default_target': default_target,
        'basedir': basedir,
        'properties': properties,
        'targets': targets
    }

    return parsed_data

def parse_element(element):
    return {
        'tag': element.tag,
        'attrib': element.attrib,
        'text': element.text,
        'children': [parse_element(child) for child in element]
    }

def format_parsed_data(parsed_data):
    formatted_data = []
    
    # Format properties section
    properties_section = "├── Properties Section\n"
    for name, value in parsed_data['properties'].items():
        properties_section += f"│   └── Property: {name} = \"{value}\"\n"
    formatted_data.append(properties_section.strip())
    
    # Format each target as a separate chunk
    for target_name, target_data in parsed_data['targets'].items():
        target_section = f"├── Target: {target_name}\n"
        for attr, value in target_data['attributes'].items():
            target_section += f"│   │   Attribute: {attr} = \"{value}\"\n"
        target_section += "│   │   └── Tasks:\n"
        for task in target_data['tasks']:
            target_section += f"│   │       Task: {task['tag']}\n"
            for attr, value in task['attrib'].items():
                target_section += f"│   │       Attribute: {attr} = \"{value}\"\n"
            if 'children' in task and task['children']:
                target_section += "│   │       Fileset:\n"
                for child in task['children']:
                    target_section += f"│   │       ['Task: {child['tag']}', 'Attribute: {list(child['attrib'].items())}']\n"
        formatted_data.append(target_section.strip())
    
    return formatted_data

def call_openai_api(formatted_data, context=""):
    prompt = f"""You are a code migration expert specializing in comprehensively converting every Ant build task to Maven build equivalent task. 
    Given well-structured Ant build file content chunk, convert it to equivalent Maven build file content chunk. 
    Ensure that all targets, properties, plugin dependencies, and other configurations are accurately translated depending on whatever is provided.
    Focus on given section and build the Maven build file content section accordingly.

Ant build file content: {formatted_data}

Accumulated Context: {context}

Expected Output: Only the maven chunk equivalent to the given Ant build file content chunk without any explanation. Don't give gibberish output as each and every chunk will be added to the final output.
"""
    # client = OpenAI(api_key="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")
    client = OpenAI("api-key")
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content

# Set 2: Consolidating Maven fragments into a complete pom.xml
def read_file(file_path):
    with open(file_path, 'r') as file:
        return file.read()

def call_openai(file_content):
    prompt = f"""You are an expert in Maven project configurations and proficient in consolidating multiple fragmented sections of a Maven `pom.xml` file into a single, complete, and functional `pom.xml`.

I have already migrated my Ant targets into corresponding Maven configurations, but the result is fragmented into multiple chunks. Your task is to:

1. Combine all provided Maven `pom.xml` fragments into a **fully consolidated and well-structured `pom.xml` file**.
2. Ensure that all `<properties>`, `<dependencies>`, `<build>`, `<plugins>`, `<executions>`, `<profiles>`, and other relevant sections are **included without duplication or omission**.
3. Arrange the final `pom.xml` in logical order:
   - `<modelVersion>`, `<groupId>`, `<artifactId>`, and `<version>` at the top.
   - `<properties>` before `<dependencies>`.
   - `<build>` and `<profiles>` at the end.
4. Maintain proper XML syntax and ensure the file is ready for use in a Maven project.

Input: The fragmented sections of the `pom.xml` file:
{file_content}

Output: Only the maven code equivalent to the combined file content without any explanation. Don't give gibberish output as this is part of data pipeline.
"""
    client = OpenAI(api_key="api-key")
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content

@app.route('/chunksconvert', methods=['POST'])
def chunksconvert():
    file_path = request.json.get('file_path')
    
    # Step 1: Parse Ant build file
    parsed_data = parse_ant_build_file(file_path)
    formatted_data = format_parsed_data(parsed_data)
    
    # Step 2: Convert to Maven fragments
    output = []
    context = ""
    for chunk in formatted_data:
        response = call_openai_api(chunk, context)
        output.append(response)
        context += response + "\n"  # Accumulate context
    
    # Step 3: Consolidate Maven fragments
    final_output = call_openai("\n".join(output))

    # Step 4: Add Parent Sections
    parent_section = """
    <parent>
        <groupId>com.ups</groupId>
        <artifactId>shared</artifactId>
        <version>1.0.0</version>
    </parent>
    """
    
    
    # Insert the sections into the final_output
    final_output = final_output.replace(
        "<project>", f"<project>{parent_section}"
    )
    
    return jsonify({"pom.xml": final_output})


def call_gpt(prompt):
    client = OpenAI(api_key="api-key")
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()

@app.route('/index')
def index():
    return render_template('index.html')

@app.route('/check-pom', methods=['GET'])
def check_pom():
    pom_exists = os.path.exists(r'D:\MavenDemo\ShippingCompany\pom.xml')
    return jsonify(pom_exists=pom_exists)

@app.route('/update_ui', methods=['POST'])
def update_ui():
    global check_final_maven_content
    data = request.get_json()
    print("Received data:", data)  # Debug print to check received data
    check_final_maven_content = data.get('check_final_maven_content')
    print("Extracted check_final_maven_content:", check_final_maven_content)  # Debug print to check extracted content
    return jsonify({'check_final_maven_content': check_final_maven_content})

@app.route('/get_final_maven_content', methods=['GET'])
def get_final_maven_content():
    global check_final_maven_content
    return jsonify({'check_final_maven_content': check_final_maven_content})

def update_ui_callback(check_final_maven_content):
    print("In update ui loop:", check_final_maven_content)
    with app.test_request_context():
        response = app.test_client().post('/update_ui', json={'check_final_maven_content': check_final_maven_content})
        print("Response JSON:", response.get_json())  # Debug print to check response JSON

@app.route('/generate', methods=['GET'])
def generate():
    repo_url = request.args.get('repo_url')
    if repo_url:
        try:
            clone_command = ["git", "clone", repo_url]
            sp.run(clone_command, capture_output=True, text=True, check=True)
            return jsonify(message="Repository cloned successfully!"), 200
        except sp.CalledProcessError as e:
            return jsonify(message=f"Failed to clone the repository:\n{e.stderr}"), 500
    else:
        return jsonify(message="Repository URL or commit message not provided."), 400

@app.route('/fileupload')
def fileupload():
    return "File upload page" 
 
# Directly assign your GitHub token here (not recommended for production)
GITHUB_TOKEN = "api-key"
@app.route('/push', methods=['POST'])
def push():
    #commit_message = request.form.get('commit_message', 'Auto commit')
    #repo_path = r"C:\Users\Shilpitha\EZFlow\UPS-POC\PocUI\xss"  # Local path to your cloned repository
    commit_message = 'Auto commit'
    repo_path = r"C:\Users\Shilpitha\Downloads\UPS-POC-Demo\AntMaven"  # Local path to your cloned repository
    branch_name = 'main'  # Update to the branch you want to push to
    try:
        # Check for changes to commit
        print("Checking status of repository...")
        status_result = sp.run(
            ["git", "-C", repo_path, "status"],
            capture_output=True, text=True
        )
        print("Repository status:", status_result.stdout)
        # Add changes
        print("Attempting to add changes...")
        add_result = sp.run(
            ["git", "-C", repo_path, "add", "."],
            capture_output=True, text=True
        )
        if add_result.returncode != 0:
            print(f"Add Error: {add_result.stderr}")
            return f"Failed to add changes:\n{add_result.stderr}", 500
        # Commit changes
        print("Attempting to commit changes...")
        commit_result = sp.run(
            ["git", "-C", repo_path, "commit", "-m", commit_message],
            capture_output=True, text=True
        )
        if commit_result.returncode != 0:
            if "nothing to commit" in commit_result.stderr:
                print("No changes to commit.")
                return "No changes to commit.", 200
            else:
                print(f"Commit Error: {commit_result.stderr}")
                return f"Failed to commit changes:\n{commit_result.stderr}", 500
        # Push changes using token for authentication
        print("Attempting to push changes...")
        push_result = sp.run(
            ["git", "-C", repo_path, "push", f"https://{GITHUB_TOKEN}@github.com/EPS2024/AntMaven.git", branch_name],
            capture_output=True, text=True
        )
        if push_result.returncode != 0:
            print(f"Push Error: {push_result.stderr}")
            return f"Failed to push changes:\n{push_result.stderr}", 500
        return "Changes pushed successfully!", 200
    except sp.CalledProcessError as e:
        print(f"General Error: {e.stderr}")
        return f"An error occurred:\n{e.stderr}", 500
    except Exception as e:
        print(f"Unexpected Error: {str(e)}")
        return f"Unexpected error:\n{str(e)}", 500
 
         

@app.route('/convert', methods=['POST'])
def convert():
    global check_final_maven_content
    print("Received POST request at /convert")
    data = request.json
    print("Request data:", data)
    
    file_path = data.get('filePath')
    library_folder_path = data.get('libraryFolderPath')
    dependency_version = data.get('dependencyVersion')
    output_file_path = data.get('outputFilePath')

    print("Before Try:")

    try:
        # List all files in the specified folder
        file_names = os.listdir(library_folder_path)
        
        # Print the list of file names
        print("Files in the folder:")
        print(file_names)

        returned_text = echo_text(dependency_version)
        print("Returned text:", returned_text)

        combined_output = file_names + [("The following are the necessary versions Dependencies to include in the code that is being generated", dependency_version)]
        print("Combined output:", combined_output)

        # Open the build.xml file in read mode
        with open(file_path, 'r') as file:
            # Read the content of the file
            content = file.read()
            print("File content read successfully.")
            send_to_gpt(content, combined_output, output_file_path,update_ui_callback)
            message = "The above issues have been handled, the duplicate dependencies have been deleted and the final Maven build file has been generated."
            response = jsonify(message=message)
            response.status_code = 200
            return response
        
    except FileNotFoundError:
        print(f"Error: The file at {file_path} was not found.")
        return jsonify({"error": f"The file at {file_path} was not found."}), 404
    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"error": str(e)}), 500

def echo_text(text):
    """
    This function takes text as input and returns the same text as output.
    """
    return text

def execute(cmd, cwd=None):
    popen = sp.Popen(cmd, stdout=sp.PIPE, universal_newlines=True, shell=True, cwd=cwd)
    for stdout_line in iter(popen.stdout.readline, ""):
        yield stdout_line
    popen.stdout.close()
    return_code = popen.wait()
    if return_code:
        raise sp.CalledProcessError(return_code, cmd)

def make_changes(output_file_path):
    with open(output_file_path, 'r') as file:
        # Read the content of the file
        to_be_changed_content = file.read()
    client = OpenAI(api_key="api-key")
    changed_response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are an Assistant specialized in making an error free Maven Code. Your task is to read the provided Maven build file and generate the corresponding Executable Maven build file. Focus on generating only the necessary and executable Maven code. Do not use triple quotes at the beginning or end of the generated Maven code."},
            {"role": "user", "content": f"Make changes to the code that is necessary only for execution, based on the following Maven build file: {to_be_changed_content}"},
            
        ]
    )
    
    response_content = changed_response.choices[0].message.content.strip()
    print("GPT-3.5 Response:\n")
    print(response_content)

    with open(output_file_path, "w") as file:
        file.write(response_content)
    return run_maven_build(output_file_path)

def send_to_gpt(content, combined_output, output_file_path, update_ui_callback):
    try:
        prompt_step1 = ant_to_maven_prompt_step1.format(content=content)
        initial_maven_content = call_gpt(prompt_step1)
        print("Initial Maven Content:\n")
        print(initial_maven_content)

        prompt_step2 = ant_to_maven_prompt_step2.format(maven_content=initial_maven_content, dependencies=combined_output)
        final_maven_content = call_gpt(prompt_step2)
        print("Final Maven Content:\n")
        print(final_maven_content)

        prompt_step3 = ant_to_maven_prompt_step3.format(maven_content=final_maven_content, dependencies=combined_output)
        add_final_maven_content = call_gpt(prompt_step3)
        print("Checked Maven Content:\n")
        print(add_final_maven_content)

        prompt_step4 = ant_to_maven_prompt_step4.format(maven_content=add_final_maven_content, dependencies=combined_output)
        check_final_maven_content = call_gpt(prompt_step4)
        print("Checked Maven Content:\n")
        print(check_final_maven_content)



        update_ui_callback(check_final_maven_content)

        with open(output_file_path, "w") as file:
            file.write(final_maven_content)
        return run_maven_build(output_file_path)

    except Exception as e:
        print(f"An error occurred while communicating with OpenAI: {e}")

    return response

def run_maven_build(output_file_path):
    pom_exists = os.path.exists(output_file_path)
    if pom_exists:
        directory_path = os.path.dirname(output_file_path)
        print(f"Running Maven build in directory: {directory_path}") 

        max_attempts = 2
        attempt = 0
        success = False
        message = ""

        log_file_path = os.path.join(directory_path, "maven_build_log.txt")
        with open(log_file_path, "w") as log_file:
            while attempt < max_attempts:
                try:
                    print(f"Running Maven build in directory: {directory_path}")

                    output_log = []
                    # Run 'mvn validate'
                    for output in execute("mvn validate", cwd=directory_path):
                        output_log.append(output)
                        log_file.write(output)
                        log_file.flush()
                        print(output, end="")
        
                    print("Maven validate successful.")
                    print("Log output:")
                    print("\n".join(output_log))
                
                    # Run 'mvn clean'
                    for output in execute("mvn clean", cwd=directory_path):
                        log_file.write(output)
                        log_file.flush()
                        print(output, end="")
        
                    print("Maven build process completed.")
                    message = "Build successful. You can now run your Project using Maven."
                    success = True
                    break
                except sp.CalledProcessError as e:
                    print(f"Validation failed on attempt {attempt + 1}: {e}")
                    make_changes(output_file_path)
                    attempt += 1
                    if attempt == max_attempts:
                        print("Maximum attempts reached. Build failed.")
                        message = "Build failed. Please check the logs for details."
                    else:
                        message = "Conversion completed, but pom.xml does not exist"

        print(f"Final message: {message}")
        status_code = 200 if success else 500
        response = jsonify(message=message)
        response.status_code = status_code
        return response
    else:
        message = "pom.xml does not exist"
        response = jsonify(message=message)
        response.status_code = 404
        return response


if __name__ == '__main__':
    app.run(debug=True)
    
