
from tqdm import tqdm
import subprocess
import mikeio
import psutil
from pathlib import Path
import numpy as np
import mikeio
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
from cartopy import crs as ccrs
import matplotlib.pyplot as plt

class Collector:
    def __init__(self, simfile, manning_file, zones):
        self.simfile = simfile
        self.zones = zones
        self.manning_file = manning_file

def read_num_timesteps(simfile) -> int:
    """
    Reads the number of timesteps from the simulation file.

    Returns:
        int: The number of timesteps for the simulation.

    """
    simfile_pfs = mikeio.read_pfs(simfile)

    time_step_field = getattr(simfile_pfs, "FemEngineHD").TIME
    return time_step_field.number_of_time_steps

def run_simulation(command, timesteps):
    timestep_old = 0

    process = subprocess.Popen(command, 
                               stdout=subprocess.PIPE, 
                               stderr=subprocess.PIPE,
                               shell=True, 
                               text=True, 
                               encoding = "cp1252",
                               bufsize=1)

    try:
        with tqdm(total=timesteps, desc="Processing", unit="step") as pbar:
            if process.stdout is not None:
                for line in process.stdout:
                    if "Time step:" in line:
                        try:
                            timestep = int(line.split(":")[1].strip())
                            pbar.update(timestep - timestep_old)
                            timestep_old = timestep

                        except ValueError as e:
                            print(f"Failed to parse timestep from line: {line.strip()} ({e})")

    except KeyboardInterrupt:
        print("Simulation interrupted. Terminating the process...")
        _terminate()
        print("Process terminated successfully.")
        raise

    if process.stderr is not None:
        for line in process.stderr:
            print(f"{line.strip()}")

    process.wait()

def _terminate(process):
    """Terminates the simulation process and any detached child processes.

    This function attempts to terminate the main simulation process and any
    detached child processes that were started within a specific time window and contain 'FemEngine'.
    """
    detached_child_processes = _get_detached_child_processes(process)

    process.terminate()
    process.wait()

    for child in detached_child_processes:
        try:
            child.terminate()
            child.wait()
        except psutil.NoSuchProcess:
            pass

def _get_detached_child_processes(process) -> list[psutil.Process]:
    """
    Retrieves a list of detached child processes associated with the simulation.

    This function identifies child processes that were created within a 30-second window
    around the start time of the main simulation process and contain 'FemEngine' in their name.

    Returns:
        list[psutil.Process]: A list of psutil.Process objects representing the detached child processes.
    """

    main_process_start_time = psutil.Process(process.pid).create_time()

    time_window_start = main_process_start_time - 30
    time_window_end = main_process_start_time + 30

    return [
        proc
        for proc in psutil.process_iter(["pid", "name", "create_time"])
        if "FemEngine" in proc.info["name"] and time_window_start <= proc.info["create_time"] <= time_window_end
    ]

def create_new_manning_file(trial_no, manning_file, zones, new_values) -> Path:

    def _create_new_manning_file_path(manning_file, trial_no) -> Path:
        if f"_trial_" not in manning_file.as_posix():
            return Path(manning_file).with_stem(f"{manning_file.stem}_trial_{trial_no}")
        else:
            return Path(manning_file).with_stem(manning_file.stem.replace(f"_trial_{trial_no - 1}", f"_trial_{trial_no}"))
        
    try:
        ds = mikeio.read(manning_file, items="manning")
    except ValueError:
        raise ValueError(f"Failed to read {manning_file}")

    new_ds = ds.copy()

    for i, zone in enumerate(zones):
        new_ds["manning"].values[zone] = new_values[i]

    new_manning_file = _create_new_manning_file_path(manning_file, trial_no)

    try:
        new_ds.to_dfs(new_manning_file)
    except Exception as e:
        raise Exception(f"Failed to write new manning file: {e}")

    return new_manning_file

def create_new_simfile(trial_no, simfile, manning_file) -> Path:
        
    def _create_new_simfile_path(simfile, trial_no) -> Path:
        if f"_trial_" not in simfile.as_posix():
            return Path(simfile).with_stem(f"{simfile.stem}_trial_{trial_no}")
        else:
            return Path(simfile).with_stem(simfile.stem.replace(f"_trial_{trial_no - 1}", f"_trial_{trial_no}"))
            
    pfs = mikeio.read_pfs(simfile)

    try:
        pfs.HD.BED_RESISTANCE.MANNING_NUMBER.file_name = f"|{manning_file.resolve()}|"

        pfs.HD.BED_RESISTANCE.MANNING_NUMBER.item_number = 1
        pfs.HD.BED_RESISTANCE.MANNING_NUMBER.item_name = "manning"
    except AttributeError as e:
        raise AttributeError(f"Error updating simfile: {e}")

    new_simfile = _create_new_simfile_path(simfile, trial_no)

    try:
        pfs.write(new_simfile)
    except Exception as e:
        raise Exception(f"Failed to write new simulation file: {e}")

    return new_simfile

def suggest_new_manning(trial, zones):

    new_values = []

    for i, zone in enumerate(zones):
        new_values.append(
            trial.suggest_float(
                f"Manning zone {i}", 
                low=0.001, 
                high=81.101, 
                step=0.01
            )
        )
    
    return new_values

def find_zones(manning_file):

    try:
        ds = mikeio.read(manning_file, items="manning")
    except ValueError:
        raise ValueError(f"Failed to read {manning_file}")

    values =  ds["manning"].values

    zones = []
    for value in np.unique(values):
        zones.append(np.where(values == value))

    return zones


def plot_zones(da, savepath):

    import matplotlib.ticker as mticker
    projPC = ccrs.PlateCarree()
    fig = plt.figure(figsize=(11, 8.5))
    ax = plt.subplot(1, 1, 1, projection=projPC)
    gl = ax.gridlines(draw_labels=True, linewidth=1, color="gray", alpha=0.5, linestyle="--")

    gl.xlocator = mticker.FixedLocator(np.arange(-2, 10, 2))
    gl.ylocator = mticker.FixedLocator(np.arange(50, 57, 1))  

    unique_values = np.unique(da.values)

    n_colors = len(unique_values)
    cmap_base = plt.get_cmap("tab20")
    colors = [cmap_base(i % cmap_base.N) for i in range(n_colors)]
    cmap = mcolors.ListedColormap(colors, name="custom_tab20")

    value_mapping = {v: i for i, v in enumerate(unique_values)}
    reassigned_values = np.vectorize(value_mapping.get)(da.values)
    da_new = mikeio.DataArray(reassigned_values, time=da.time, geometry=da.geometry)

    da_new.plot(
        ax=ax,
        cmap=cmap,
        add_colorbar=False,
    )

    legend_patches = []
    for i, val in enumerate(unique_values):
        color = cmap(i)
        patch = mpatches.Patch(color=color, label=f"Zone {i}")
        legend_patches.append(patch)

    ax.legend(
        handles=legend_patches,
        title="Zones",
        loc="lower right",
        fontsize="small",  # Smaller text
        frameon=True,  # Add bounding box
        fancybox=True,  # Rounded edges
        edgecolor="black",  # Black border
        framealpha=0.8,  # Slight transparency
        # bbox_to_anchor=(1.15, 1),  # Adjust position if needed
        ncol=2,  # Two-column legend layout
        columnspacing=1.0,  # Adjusts spacing between columns
        borderpad=0.5,  # Space inside the legend box
    )

    fig.set_size_inches(11, 8.5)

    ax.set_aspect(1)
    ax.set_ylim([49.2, 56.3])
    ax.set_xlim([-3, 9.2])

    ax.set_title("")

    if savepath is not None:
        fig.savefig(savepath, dpi=300, bbox_inches="tight")