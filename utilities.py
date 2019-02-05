# This file contains functions and classes which can be classified as utilitie functions for a more
# general purpose. Furthermore, this functions are for python analysis for ALIBAVA files.

__version__ = 0.1
__date__ = "13.12.2018"
__author__ = "Dominic Bloech"
__email__ = "dominic.bloech@oeaw.ac.at"

# Import statements
import sys
import os
from tqdm import tqdm
import h5py
import yaml
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.pyplot as plt
from warnings import warn
from six.moves import cPickle as pickle #for performance
import struct

def create_dictionary(file, filepath):
    '''Creates a dictionary with all values written in the file using yaml'''

    file_string = os.path.abspath(os.getcwd() + str(filepath) + "\\" + str(file))
    print ("Loading file: " + str(file))
    with open(file_string, "r") as yfile:
        dict = yaml.load(yfile)
        return dict

def import_h5(*pathes):
    """
    This functions imports hdf5 files generated by ALIBAVA.
    If you pass several pathes, then you get list of objects back, containing the data respectively
    :param pathes: pathes to the datafiles which should be imported
    :return: list
    """

    # Check if a list was passed
    if type(pathes[0]) == list:
        pathes = pathes[0]

    # First check if pathes exist and if so import
    loaded_files = []
    try:
        for path in tqdm(pathes, desc= "Loading files:"):
            if os.path.exists(os.path.normpath(path)):
                # Now import all hdf5 files
                loaded_files.append(h5py.File(os.path.normpath(path), 'r'))
            else:
                raise Exception('The path {!s} does not exist.'.format(path))
        return loaded_files
    except OSError as e:
        print("Enountered an OSError: {!s}".format(e))
        return False

def get_xy_data(data, header=0):
    """This functions takes a list of strings, containing a header and xy data, return values are 2D np.array of the data and the header lines"""

    np2Darray = np.zeros((len(data)-int(header),2), dtype=np.float32)
    for i, item in enumerate(data):
        if i > header-1:
            list_data = list(map(float,item.split()))
            np2Darray[i-header] = np.array(list_data)
    return np2Darray

def read_binary_Alibava(filepath):
    """Reads binary alibava files"""

    with open(os.path.normpath(filepath), "rb") as f:
        header = f.read(16)
        Starttime = struct.unpack("II", header[0:8])[0]  # Is a uint32
        Runtype = struct.unpack("i", header[8:12])[0]  # int32
        Headerlength = struct.unpack("I", header[12:16])
        header = f.read(Headerlength[0])
        Header = struct.unpack("{}s".format(Headerlength[0]), header)[0].decode("Utf-8")
        Pedestal = np.array(struct.unpack("d" * 256, f.read(8 * 256)), dtype=np.float32)
        Noise = np.array(struct.unpack("d" * 256, f.read(8 * 256)), dtype=np.float32)
        byteorder = sys.byteorder

        # Data Blocks
        # Read all data Blocks
        # Warning Alibava Binary calibration files have no indicatior how many events are really inside the file
        # The eventnumber corresponds to the pulse number --> Readout of files have to be done until end of file is reached
        # and the eventnumber must be calculated --> Advantage: Damaged files can be read as well
        #events = Header.split("|")[1].split(";")[0]
        event_data = []
        events = 0
        eof = False
        #for event in range(int(events)):
        while not eof:
            blockheader = f.read(4)  # should be 0xcafe002
            if blockheader == b'\x02\x00\xfe\xca' or blockheader == b'\xca\xfe\x00\x02':
                events += 1
                blocksize = struct.unpack("I", f.read(4))
                event_data.append(f.read(blocksize[0]))
            else:
                print("Warning: While reading data Block {}. Header was not the 0xcafe0002 it was {!s}".format(events, str(blockheader)))
                if not blockheader:
                    print("Persumably end of binary file reached. Events read: {}".format(events))
                    eof = True

        dict = {"header": {
                            "noise": Noise,
                            "pedestal": Pedestal,
                            "Attribute:setup": None
                            },
                "events": {
                            "header": Header,
                            "signal": np.zeros((int(events),256), dtype=np.float32),
                            "temperature": np.zeros(int(events), dtype=np.float32),
                            "time": np.zeros(int(events), dtype=np.float32),
                            "clock": np.zeros(int(events), dtype=np.float32)
                            },
                "scan": {
                        "start": Starttime,
                        "end": None,
                        "value": None, # Values of cal files for example. eg. 32 pulses for a charge scan steps should be here
                        "attribute:scan_definition": None
                        }
                }

        # Disect the header for the correct informations for values
        points = Header.split("|")[1].split(";")
        params = [x.strip("\x00") for x in points]

        # Alibava binary have (unfortunately) a non consistend header format
        # Therefore, we have to distinguish between the two formats --> len(params) = 4 --> Calibration
        # len(params) = 2 --> Eventfile

        if len(params) >= 4: # Cal file
            dict["scan"]["value"] = np.arange(int(params[1]), int(params[2]), int(params[3]))  # aka xdata
        elif len(params) == 2: # Events file
            dict["scan"]["value"] = np.arange(0, int(params[0]),step=1)  # aka xdata

        shift1 = int.from_bytes(b'0xFFFF0000',byteorder=byteorder)
        shift2 = int.from_bytes(b'0xFFFF',byteorder=byteorder)
        # decode data from data Blocks
        for i, event in enumerate(event_data):
            dict["events"]["clock"][i] = struct.unpack("III", event[0:12])[-1]
            coded_time = struct.unpack("I", event[12:16])[0]
            #coded_time = event[12:16]
            ipart = (coded_time & shift1)>>16
            fpart = (np.sign(ipart))*(coded_time & shift2)
            time = 100*ipart+fpart
            dict["events"]["time"][i] = time
            dict["events"]["temperature"][i] = 0.12*struct.unpack("H", event[16:18])[0]-39.8

            # There seems to be garbage data which needs to be cut out
            padding = 18+32
            part1 = list(struct.unpack("h"*128, event[padding:padding+2*128]))
            padding += 2*130+28
            part2 = list(struct.unpack("h" * 128, event[padding:padding + 2*128]))
            part1.extend(part2)
            dict["events"]["signal"][i] = np.array(part1)
            #dict["events"]["signal"][i] =struct.unpack("H"*256, event[18:18+2*256])
            #extra = struct.unpack("d", event[18+2*256:18+2*256+4])[0]

    return dict

def read_file(filepath, binary=False):
    """Just reads a file and returns the content line by line"""
    if os.path.exists(os.path.normpath(filepath)):
        if not binary:
            with open(os.path.normpath(filepath), 'r') as f:
                read_data = f.readlines()
            return read_data
        else:
            return read_binary_Alibava(filepath)

    else:
        print("No valid path passed: {!s}".format(filepath))
        return None

def clustering(self, estimator):
    """Does the clustering up to the max cluster number, you just need the estimator and its config parameters"""
    return estimator

def count_sub_length(ndarray):
    """This function count the length of sub elements (depth 1) in the ndarray and returns an array with the lengthes
    with the same dimension as ndarray"""
    results = np.zeros(len(ndarray))
    for i in range(len(ndarray)):
        if len(ndarray[i]) == 1:
            results[i] = len(ndarray[i][0])
    return results


def convert_ADC_to_e(signal, interpolation_function):
    """
    Gets the signal in ADC and the interpolatrion function and returns an array with the interpolated singal in electorns
    :param signal: Singnal array which should be converted, basically the singal from every strip
    :param interpolation_function: the interpolation function
    :return: Returns array with the electron count
    """
    return interpolation_function(np.abs(signal))

def save_all_plots(name, folder, figs=None, dpi=200):
    """
    This function saves all generated plots to a specific folder with the defined name in one pdf
    :param name: Name of output
    :param folder: Output folder
    :param figs: Figures which you want to save to one pdf (leaf empty for all plots) (list)
    :param dpi: image dpi
    :return: None
    """
    try:
        pp = PdfPages(os.path.normpath(folder) + "\\" + name + ".pdf")
    except PermissionError:
        print("While overwriting the file {!s} a permission error occured, please close file if opened!".format(name + ".pdf"))
        return
    if figs is None:
        figs = [plt.figure(n) for n in plt.get_fignums()]
    for fig in tqdm(figs, desc="Saving plots"):
        fig.savefig(pp, format='pdf')
    pp.close()

class NoStdStreams(object):
    """Surpresses all output of a function when called with with """
    def __init__(self,stdout = None, stderr = None):
        self.devnull = open(os.devnull,'w')
        self._stdout = stdout or self.devnull or sys.stdout
        self._stderr = stderr or self.devnull or sys.stderr

    def __enter__(self):
        self.old_stdout, self.old_stderr = sys.stdout, sys.stderr
        self.old_stdout.flush(); self.old_stderr.flush()
        sys.stdout, sys.stderr = self._stdout, self._stderr

    def __exit__(self, exc_type, exc_value, traceback):
        self._stdout.flush(); self._stderr.flush()
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr
        self.devnull.close()

def gaussian(x, mu, sig, a):
    return a*np.exp(-np.power(x - mu, 2.) / (2. * np.power(sig, 2.)))

def langau_cluster(cls_ind, valid_events_Signal, valid_events_clusters, charge_cal, noise):
    """Calculates the energy of events, clustersize independend"""
    # for size in tqdm(clustersize_list, desc="(langau) Processing clustersize"):
    totalE = np.zeros(len(cls_ind))
    totalNoise = np.zeros(len(cls_ind))
    # Loop over the clustersize to get total deposited energy
    incrementor = 0
    for ind in tqdm(cls_ind, desc="(langau) Processing event"):
        # TODO: make this work for multiple cluster in one event
        # Signal calculations
        signal_clst_event = np.take(valid_events_Signal[ind], valid_events_clusters[ind][0])
        totalE[incrementor] = np.sum(convert_ADC_to_e(signal_clst_event, charge_cal))

        # Noise Calculations
        noise_clst_event = np.take(noise, valid_events_clusters[ind][0])  # Get the Noise of an event
        totalNoise[incrementor] = np.sqrt(np.sum(convert_ADC_to_e(noise_clst_event, charge_cal)))  # eError is a list containing electron signal noise

        incrementor += 1

    preresults = {}
    preresults["signal"] = totalE
    preresults["noise"] = totalNoise
    return preresults

def get_size(obj, seen=None):
    """Recursively finds size of objects"""
    size = sys.getsizeof(obj)
    if seen is None:
        seen = set()
    obj_id = id(obj)
    if obj_id in seen:
        return 0
    # Important mark as seen *before* entering recursion to gracefully handle
    # self-referential objects
    seen.add(obj_id)
    if isinstance(obj, dict):
        size += sum([get_size(v, seen) for v in obj.values()])
        size += sum([get_size(k, seen) for k in obj.keys()])
    elif hasattr(obj, '__dict__'):
        size += get_size(obj.__dict__, seen)
    elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes, bytearray)):
        size += sum([get_size(i, seen) for i in obj])
    return size


class Bdata:
    """Creates an object which can handle numpy arrays. By passing lables you can get the columns of the multidimensional array.
    Its like a pandas array but with way less overhead.
    If you store a Bdata object you can get columns by accessing it via Bdata['label']
    Not passing an argument results in """

    def __init__(self, data = np.array([]), labels = None):
        self.data = data
        self.labels = labels

        if len(self.data) != len(self.labels):
            warn("Data missmatch!")

    def __getitem__(self, arg=None):
        if arg:
            return self.get(arg)

    def __repr__(self):
        return repr(self.data)

    def get(self, label):
        return self.data[:,self.labels.index(label)]

def save_dict(di_, filename_):
    with open(os.path.normpath(filename_), 'wb') as f:
        pickle.dump(di_, f)

def load_dict(filename_):
    with open(os.path.normpath(filename_), 'rb') as f:
        ret_di = pickle.load(f)
    return ret_di


if __name__ == "__main__":
    read_file('C:\\Users\\dbloech\\Desktop\\run002_1E4.dat', True)
