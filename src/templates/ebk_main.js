function get_dir_options(input, select, type, directory) {
    console.log("Updating dir: type = " + type + " ; dir = " + directory);
    $.post("/update_dir", {id: type, dir: directory}, function (response) {
        console.log("Updated dir: type = " + type + " ; dir = " + response.dir);
        input.value = response.dir;
        $.post('/list_subfolders', {"cur_dir": response.dir}, function (response) {
            let folders = response['folders'];
            console.log("subfolders = " + folders);
            let options = select.options;
            for (let i = options.length - 1; i >= 0; i--) {
                select.remove(i);
            }
            for (let folder of folders) {
                select.options.add(new Option(folder, folder));
            }
        });
    })
}

// update target directory with text input
function input_dir(event) {
    console.log(event.target);
    let input = event.target;
    let select = input.nextElementSibling;
    let type = input.id;
    let directory = input.value;
    get_dir_options(input, select, type, directory);
}

// update target directory with drop down menu
function select_dir(event) {
    let select = event.target;
    let input = select.previousElementSibling;
    let type = input.id;
    let directory = select.value;
    get_dir_options(input, select, type, directory);
}

function input_email(event) {
    console.log(event.target);
    $.post("/update_config", {key: event.target.id, value: event.target.value}, function (response) {
        if (response.code === 200) {
            console.log("Updated " + event.target.id + " = " + event.target.value)
        }
    })
}
