# This file contains functions for noise and pedestal analysis of ALIBAVA files

__version__ = 0.1
__date__ = "13.12.2018"
__author__ = "Dominic Bloech"
__email__ = "dominic.bloech@oeaw.ac.at"

# Import statements
from utilities import *
from tqdm import tqdm
import numpy as np
from scipy.stats import norm
import matplotlib.pyplot as plt
from scipy.interpolate import CubicSpline, PchipInterpolator
from scipy.optimize import curve_fit
import pylandau
from nb_analysisFunction import *
from time import time
import iminuit


class event_analysis:
    """This class analyses measurement files per event"""

    def __init__(self, path_list = None, **kwargs):
        """

        :param path_list: List of pathes to analyse
        :param kwargs: kwargs if further data should be used, possible kwargs=calibration,noise
        """

        # Init parameters
        print("Loading event file(s): {!s}".format(path_list))
        self.data = import_h5(path_list)

        self.numchan = len(self.data[0]["events/signal"][0])
        self.numevents = len(self.data[0]["events/signal"])
        self.pedestal = np.zeros(self.numchan, dtype=np.float64)
        self.noise = np.zeros(self.numchan, dtype=np.float64)
        self.SN_cut = 1
        self.hits = 0
        self.tmin = 0
        self.tmax = 100
        self.maxcluster = 4
        self.CMN = np.zeros(self.numchan, dtype=np.float64)
        self.CMsig = np.zeros(self.numchan, dtype=np.float64)
        self.outputdata = {}
        self.automasked_hit = 0
        self.events = 0
        self.total_events = self.numevents*len(self.data)
        self.additional_analysis = []




        if "configs" in kwargs:
            kwargs = kwargs["configs"] # If a config was passeds it has to be a dict containig all settings therefore kwargs rewritten

        # For additional analysis
        self.add_analysis = kwargs.get("additional_analysis", [])

        # Material decision
        self.material = kwargs.get("sensor_type", "n-in-p")
        if self.material == "n-in-p":
            self.material = 1
        else:
            self.material = 0

        self.masking = kwargs.get("automasking", False)
        self.max_clustersize = kwargs.get("max_cluster_size", 5)
        self.SN_ratio = kwargs.get("SN_ratio", 0.5)
        self.usejit = kwargs.get("optimize", False)
        self.calibration = kwargs.get("calibration", None)


        if "pedestal" in kwargs:
            self.pedestal = kwargs["pedestal"]

        if "SN_cut" in kwargs:
            self.SN_cut = kwargs["SN_cut"] # Cut for the signal to noise ratio

        if "CMN" in kwargs:
            self.CMN = kwargs["CMN"] # CMN for every channel and event

        if "CMsig" in kwargs:
            self.CMsig = kwargs["CMsig"] # Common mode sig for every channel

        if "Noise" in kwargs:
            self.noise = kwargs["Noise"] # Noise for every channel and event

        if "timing" in kwargs:
            self.min = kwargs["timing"][0] # timinig window
            self.max = kwargs["timing"][1] # timing maximum


        print("Processing files ...")
        # Here a loop over all files will be done to do the analysis on all imported files
        for data in tqdm(range(len(self.data)), desc="Data files processed:"):
                events = self.data[data]["events/signal"][:]
                timing = self.data[data]["events/time"][:]
                # Todo: Make this loop work in a pool of processes/threads whichever is easier and better
                results = np.array(self.do_analysis(events, timing)) # you get back a list with events, containing the event processed data --> np array makes it easier to slice
                # No make the data easy accessible: results(array) --> entries are events --> containing data eg indes 0 ist signal
                # So now order the data Dictionary --> Filename:Type of data: List of all events for specific data type ---> results[: (take all events), 0 (give me data from signal]
                # Resulting is an array containing all singal data etc.
                self.outputdata[str(self.data[data]).split('"')[1].split('.')[0]] = {"Signal": results[:,0],
                                                                                     "SN": results[:, 1],
                                                                                     "CMN": results[:, 2],
                                                                                     "CMsig": results[:, 3],
                                                                                     "Hitmap": results[:, 4],
                                                                                     "Channel_hit": results[:, 5],
                                                                                     "Clusters": results[:, 6],
                                                                                     "Clustersize": results[:, 8],
                                                                                     "Numclus": results[:, 7]}


        # Now process additional analysis statet in the config file
        for analysis in self.add_analysis:
            print("Starting analysis: {!s}".format(analysis))
            add_analysis = eval(analysis)(self) # Gets the total analysis class, so be aware of changes inside!!!
            result = add_analysis.run()
            add_analysis.plot()

        # In the end give a round up of all you have done
        print("*************************************************************************\n" 
                  "            Analysis report:                                             \n"
                  "            ~~~~~~~~~~~~~~~~                                             \n"
                  "                                                                         \n"
                  "            Automasked hits:   {automasked!s}                            \n"
                  "            Events processed:  {events!s}                                \n"
                  "            Total events:      {total_events!s}                          \n"
                  "                                                                         \n"
                  "*************************************************************************\n".format(
                                                                                                    automasked=self.automasked_hit,
                                                                                                    events=self.events,
                                                                                                    total_events = self.total_events)
                                                                                                    )

    def do_analysis(self, events, timing):
        """Does the actual event analysis"""

        # get events with good timinig only gtime and only process these events
        gtime = np.nonzero(timing>self.tmin)
        self.events += int(gtime[0].shape[0])
        meanCMN = np.mean(self.CMN)
        meanCMsig = np.mean(self.CMsig)
        prodata = []  # List of processed data which then can be accessed
        hitmap = np.zeros(self.numchan)
        #Warning: If you have a RS and pulseshape recognition enabled the timing window has to be set accordingly

        if not self.usejit:
            # Non jitted version
            start = time()
            for event in tqdm(range(gtime[0].shape[0]), desc="Events processed:"): # Loop over all good events
            # Event and Cluster Calculations
                signal, SN, CMN, CMsig = self.process_event(events[event], self.pedestal, meanCMN, meanCMsig,self.noise, self.numchan)
                channels_hit, clusters, numclus, clustersize = self.clustering(signal, SN)
                for channel in channels_hit:
                    hitmap[channel] += 1

                prodata.append([
                    signal,
                    SN,
                    CMN,
                    CMsig,
                    hitmap,
                    channels_hit,
                    clusters,
                    numclus,
                    clustersize]
                )

        else:
            start = time()
            # Use lightspeed fast calculation, FUCK YEAH
            if False: # The parallel version does basically the same what is here unreachable
                for event in tqdm(range(gtime[0].shape[0]), desc="Events processed:"):  # Loop over all good events
                    signal, SN, CMN, CMsig = nb_process_event(events[event], self.pedestal, meanCMN, meanCMsig, self.noise, self.numchan)
                    channels_hit, clusters, numclus, clustersize, automasked_hits = nb_clustering(signal, SN, self.SN_cut, self.SN_ratio, self.numchan, max_clustersize = self.max_clustersize, masking=self.masking, material=self.material)
                    self.automasked_hit += automasked_hits
                    for channel in channels_hit:
                        hitmap[channel] += 1

                    prodata.append([
                        signal,
                        SN,
                        CMN,
                        CMsig,
                        hitmap,
                        channels_hit,
                        clusters,
                        numclus,
                        clustersize]
                    )
            else:
                # This should, in theory, use parallelization of the loop over event but i did not see any performance boost, maybe you can find the bug =)?
                data, automasked_hits= parallel_event_processing(gtime, events, self.pedestal, meanCMN, meanCMsig, self.noise, self.numchan, self.SN_cut, self.SN_ratio, max_clustersize = self.max_clustersize, masking=self.masking, material=self.material)
                prodata = list(data)
                self.automasked_hit = automasked_hits

        end = time()
        print("Time taken: {!s} seconds".format(round(abs(end - start), 2)))
        return prodata

    def clustering(self, event, SN):
        """Looks for cluster in a event"""
        channels = np.nonzero(np.abs(SN) > self.SN_cut)[0]# Only channels which have a signal/Noise higher then the signal/Noise cut

        if self.masking:
            if self.material:
                # Todo: masking of dead channels etc.
                masked_ind = np.nonzero(np.take(event, channels) > 0)[0] # So only negative values are considered
                if len(masked_ind):
                    channels = np.delete(channels, masked_ind)
                    self.automasked_hit += len(masked_ind)
            else:
                masked_ind = np.nonzero(np.take(event, channels) < 0)[0] # So only positive values are considered
                if len(masked_ind):
                    channels = np.delete(channels, masked_ind)
                    self.automasked_hit += len(masked_ind)

        used_channels = np.zeros(self.numchan) # To keep track which channel have been used already
        numclus = 0 # The number of found clusters
        clusters_list = []
        clustersize = np.array([])
        for ch in channels: # Loop over all left channels which are a hit, here from "left" to "right"
            if not used_channels[ch]: # Make sure we dont count everything twice
                used_channels[ch] = 1 # So now the channel is used
                numclus += 1
                cluster = [ch] # Keep track of the individual clusters
                size = 1

                # Now make a loop to find neighbouring hits of cluster, we must go into both directions
                # TODO huge clusters can be misinterpreted!!! Takes huge amount of cpu, vectorize
                offset = int(self.max_clustersize / 2)
                for i in range(ch-offset, ch+offset): # Search plus minus the channel found
                    if 0 < i < self.numchan: # To exclude overrun
                            if np.abs(SN[i]) > self.SN_cut * self.SN_ratio and not used_channels[i]:
                                cluster.append(i)
                                used_channels[i] = 1
                                size += 1
                clusters_list.append(cluster)
                clustersize = np.append(clustersize, size) #TODO: This cost maybe to much calculation power for to less gain
        return channels, clusters_list, numclus, clustersize

    def process_event(self, event, pedestal, meanCMN, meanCMsig, noise, numchan=256):
        """Processes single events"""

        # Calculate the common mode noise for every channel
        signal = event - pedestal  # Get the signal from event and subtract pedestal

        # Remove channels which have a signal higher then 5*CMsig+CMN which are not representative
        prosignal = np.take(signal, np.nonzero(signal<(5*meanCMsig+meanCMN))) # Processed signal

        if prosignal.any():
            cmpro = np.mean(prosignal)
            sigpro = np.std(prosignal)

            corrsignal = signal - cmpro
            SN = corrsignal / noise

            return corrsignal, SN, cmpro, sigpro
        else:
            return np.zeros(numchan), np.zeros(numchan), 0, 0 # A default value return if everything fails

    def plot_data(self, single_event = -1):
        """This function plots all data processed"""

        for name, data in self.outputdata.items():
            # Plot a single event from every file
            if single_event > 0:
                self.plot_single_event(single_event, name)

            # Plot Analysis results
            fig = plt.figure("Analysis file: {!s}".format(name))

            # Plot Hitmap
            channel_plot = fig.add_subplot(211)
            channel_plot.bar(np.arange(self.numchan), data["Hitmap"][len(data["Hitmap"])-1], 1., alpha=0.4, color="b")
            channel_plot.set_xlabel('channel [#]')
            channel_plot.set_ylabel('Hits [#]')
            channel_plot.set_title('Hitmap')

            fig.tight_layout()


            # Plot Clustering results
            fig = plt.figure("Clustering Analysis on file: {!s}".format(name))

            # Plot Number of clusters
            numclusters_plot = fig.add_subplot(221)
            bin, counts = np.unique(data["Numclus"], return_counts=True)
            numclusters_plot.bar(bin , counts, alpha=0.4, color="b")
            numclusters_plot.set_xlabel('Number of clusters [#]')
            numclusters_plot.set_ylabel('Occurance [#]')
            numclusters_plot.set_title('Number of clusters')

            # Plot clustersizes
            clusters_plot = fig.add_subplot(222)
            # Todo: make it possible to count clusters in multihit scenarios
            bin, counts = np.unique(np.concatenate(data["Clustersize"]), return_counts=True)
            clusters_plot.bar(bin, counts, alpha=0.4, color="b")
            clusters_plot.set_xlabel('Clustersize [#]')
            clusters_plot.set_ylabel('Occurance [#]')
            clusters_plot.set_title('Clustersizes')

            fig.tight_layout()

    def plot_single_event(self, eventnum, file):
        """ Plots a single event and its data"""

        data = self.outputdata[file]

        fig = plt.figure("Event number {!s}, from file: {!s}".format(eventnum, file))

        # Plot signal
        channel_plot = fig.add_subplot(211)
        channel_plot.bar(np.arange(self.numchan), data["Signal"][eventnum], 1., alpha=0.4, color="b")
        channel_plot.set_xlabel('channel [#]')
        channel_plot.set_ylabel('Signal [ADC]')
        channel_plot.set_title('Signal')

        # Plot signal/Noise
        SN_plot = fig.add_subplot(212)
        SN_plot.bar(np.arange(self.numchan), data["SN"][eventnum], 1., alpha=0.4, color="b")
        SN_plot.set_xlabel('channel [#]')
        SN_plot.set_ylabel('Signal/Noise [ADC]')
        SN_plot.set_title('Signal/Noise')

        fig.tight_layout()
        plt.draw()

class calibration:
    """This class handles all concerning the calibration"""

    def __init__(self, delay_path = "", charge_path = ""):
        """
        :param delay_path: Path to calibration file
        :param charge_path: Path to calibration file
        """

        self.charge_cal = None
        self.delay_cal = None
        self.delay_data = None
        self.charge_data = None

        self.charge_calibration_calc(charge_path)
        self.delay_calibration_calc(delay_path)


    def delay_calibration_calc(self, delay_path):
        # Delay scan
        print("Loading delay file: {!s}".format(delay_path))
        self.delay_data = read_file(delay_path)
        if self.delay_data:
            self.delay_data = get_xy_data(self.delay_data, 2)

            if self.delay_data.any():
                # Interpolate data with cubic spline interpolation
                self.delay_cal = CubicSpline(self.delay_data[:,0],self.delay_data[:,1], extrapolate=True)

    def charge_calibration_calc(self, charge_path):
        # Charge scan
        print("Loading charge calibration file: {!s}".format(charge_path))
        self.charge_data = read_file(charge_path)
        if self.charge_data:
            self.charge_data = get_xy_data(self.charge_data, 2)

            if self.charge_data.any():
                # Interpolate data with cubic spline interpolation
                self.charge_cal = PchipInterpolator(self.charge_data[:,1],self.charge_data[:,0], extrapolate=True)


    def plot_data(self):
        """Plots the processed data"""

        try:
            fig = plt.figure("Calibration")

            # Plot delay
            delay_plot = fig.add_subplot(212)
            delay_plot.bar(self.delay_data[:,0], self.delay_data[:,1], 5., alpha=0.4, color="b")
            delay_plot.plot(self.delay_data[:, 0], self.delay_cal(self.delay_data[:, 0]), "r--", color="g")
            delay_plot.set_xlabel('time [ns]')
            delay_plot.set_ylabel('Signal [ADC]')
            delay_plot.set_title('Delay plot')

            # Plot charge
            charge_plot = fig.add_subplot(211)
            charge_plot.bar(self.charge_data[:, 0], self.charge_data[:, 1], 2000., alpha=0.4, color="b")
            cal_range = np.array(np.arange(1., 700., 10.))
            charge_plot.plot(self.charge_cal(cal_range), cal_range, "r--", color="g")
            #charge_plot.plot(self.charge_cal(self.charge_data[:, 1]), self.charge_data[:, 1], "r--", color="g")
            charge_plot.set_xlabel('Charge [e-]')
            charge_plot.set_ylabel('Signal [ADC]')
            charge_plot.set_title('Charge plot')

            fig.tight_layout()
            plt.draw()
        except Exception as e:
            print("An error happened while trying to plot calibration data ", e)

class noise_analysis:
    """This class contains all calculations and data concerning pedestals in ALIBAVA files"""

    def __init__(self, path = "", usejit=False):
        """
        :param path: Path to pedestal file
        """

        # Init parameters
        print("Loading pedestal file: {!s}".format(path))
        self.data = import_h5(path)

        if self.data:
            # Some of the declaration may seem unecessary but it clears things up when you need to know how big some arrays are
            self.data=self.data[0]# Since I always get back a list
            self.numchan = len(self.data["header/pedestal"][0])
            self.numevents = len(self.data["events/signal"])
            self.pedestal = np.zeros(self.numchan, dtype=np.float64)
            self.noise = np.zeros(self.numchan, dtype=np.float64)
            self.goodevents = np.nonzero(self.data['/events/time'][:] >= 0)  # Only use events with good timing, here always the case
            self.CMnoise = np.zeros(len(self.goodevents[0]), dtype=np.float64)
            self.CMsig = np.zeros(len(self.goodevents[0]), dtype=np.float64)
            self.score = np.zeros((len(self.goodevents[0]), self.numchan), dtype=np.float64)  # Variable needed for noise calculations

            # Calculate pedestal
            print("Calculating pedestal and Noise...")
            self.pedestal = np.mean(self.data['/events/signal'][0:], axis=0)

            # Noise Calculations
            if not usejit:
                start = time()
                self.score, self.CMnoise, self.CMsig = self.noise_calc(self.data['/events/signal'][:], self.pedestal[:], self.numevents, self.numchan)
                end = time()
                print("Time taken: {!s} seconds".format(round(abs(end - start), 2)))
            else:
                print("Jit version used!!! No progress bar can be shown")
                start = time()
                self.score, self.CMnoise, self.CMsig = nb_noise_calc(self.data['/events/signal'][:], self.pedestal[:], self.numevents, self.numchan)
                end = time()
                print("Time taken: {!s} seconds".format(round(abs(end-start), 2)))
            self.noise = np.std(self.score, axis=0)  # Calculate the actual noise for every channel by building the mean of all noise from every event
        else:
            print("No valid file, skipping pedestal run")


    def noise_calc(self, events, pedestal, numevents, numchannels):
        """Noise calculation, normal noise (NN) and common mode noise (CMN)
        Uses numpy, can be further optimized by reducing memory access to member variables.
        But got 36k events per second.
        So fuck it.
        This function is not numba optimized!!!"""
        score = np.zeros((numevents, numchannels), dtype=np.float64)  # Variable needed for noise calculations
        CMnoise = np.zeros(numevents, dtype=np.float64)
        CMsig = np.zeros(numevents, dtype=np.float64)

        for event in tqdm(range(self.goodevents[0].shape[0]), desc="Events processed:"): # Loop over all good events

            # Calculate the common mode noise for every channel
            cm = events[event][:] - pedestal  # Get the signal from event and subtract pedestal
            CMNsig = np.std(cm)  # Calculate the standard deviation
            CMN = np.mean(cm)  # Now calculate the mean from the cm to get the actual common mode noise

            # Calculate the noise of channels
            cn = cm - CMN # Subtract the common mode noise --> Signal[arraylike] - pedestal[arraylike] - Common mode

            score[event] = cn
            # Append the common mode values per event into the data arrays
            CMnoise[event] = CMN
            CMsig[event] = CMNsig

        return score, CMnoise, CMsig # Return everything

    def plot_data(self):
        """Plots the data calculated by the framework"""

        fig = plt.figure("Noise analysis")

        #Plot noisedata
        noise_plot = fig.add_subplot(221)
        noise_plot.bar(np.arange(self.numchan), self.noise, 1., alpha=0.4, color="b")
        noise_plot.set_xlabel('Channel [#]')
        noise_plot.set_ylabel('Noise [ADC]')
        noise_plot.set_title('Noise levels per Channel')
        #noise_plot.legend()

        # Plot pedestal
        pede_plot = fig.add_subplot(222)
        pede_plot.bar(np.arange(self.numchan), self.pedestal, 1.,
                               yerr=self.noise, error_kw=dict(elinewidth=0.2, ecolor='r', ealpha=0.1), alpha=0.4, color="b")
        pede_plot.set_xlabel('Channel [#]')
        pede_plot.set_ylabel('Pedestal [ADC]')
        pede_plot.set_title('Pedestal levels per Channel with noise')
        pede_plot.set_ylim(bottom=min(self.pedestal)-50.)
        #pede_plot.legend()

        # Plot Common mode
        CM_plot = fig.add_subplot(223)
        n, bins, patches = CM_plot.hist(self.CMnoise, bins=50, density=True, alpha=0.4, color="b")
        # Calculate the mean and std
        mu, std = norm.fit(self.CMnoise)
        # Calculate the distribution for plotting in a histogram
        p = norm.pdf(bins, loc=mu, scale=std)
        CM_plot.plot(bins, p, "r--", color="g")

        CM_plot.set_xlabel('Common mode [ADC]')
        CM_plot.set_ylabel('[%]')
        CM_plot.set_title(r'$\mathrm{Common\ mode\:}\ \mu=' + str(round(mu,2)) + r',\ \sigma=' + str(round(std,2)) + r'$')
        #CM_plot.legend()

        fig.tight_layout()
        plt.draw()

class langau:
    """This class calculates the langau distribution and returns the best values for landau and Gauss fit to the data
    """

    def __init__(self, main_analysis):
        """Gets the main analysis class and imports all things needed for its calculations"""

        import pylandau # imports the necessary class for its calculations
        from scipy.optimize import curve_fit

        self.main = main_analysis
        self.data = self.main.outputdata.copy()
        self.results_dict = {} # Containing all data processed
        self.pedestal = self.main.pedestal

    def run(self):
        """Calculates the langau for the specified data"""

        # Go over all datafiles
        for data in tqdm(self.data, desc="(langau) Processing file:"):
            self.results_dict[data] = {}
            indizes = self.get_clusters(self.data[data], 1)[0] # Todo: make it accessible from the config
            totalE = np.zeros(len(indizes))

            # Loop over the clustersize to get total deposited energy
            incrementor = 0
            for ind in tqdm(indizes, desc="(langau) Processing event:"):
                # TODO: make this work for multiple cluster in one event
                signal_clst_event = np.take(self.data[data]["Signal"][ind], self.data[data]["Clusters"][ind][0]) # Get the signal of an event
                pedestal_clst_event = np.take(self.pedestal, self.data[data]["Clusters"][ind][0]) # Get the signal of the pedestal
                totalSignal = signal_clst_event + pedestal_clst_event # For the actual adc signal
                eSignal = convert_ADC_to_e(totalSignal, self.main.calibration.charge_cal) # eSingal is a list containing electron signal for everypassed list element
                ePedestal = convert_ADC_to_e(pedestal_clst_event, self.main.calibration.charge_cal) # eSingal is a list containing electron signal for everypassed list element
                finalSignal = np.abs(eSignal-ePedestal) # Subtract the actual signal with the offset of the pedestal
                totalE[incrementor] = np.sum(finalSignal)
                incrementor += 1

            # TODO: error estimation over noise is missing
            self.results_dict[data]["signal"] = totalE

            # Fit the langau to it
            coeff, pcov, hist = self.fit_langau(totalE)

            self.results_dict[data]["langau_coeff"] = coeff # mpv, eta, sigma, A
            self.results_dict[data]["langau_data"] = [np.arange(1.,100000., 1000.), pylandau.langau(np.arange(1.,100000., 1000.), *coeff)] # aka x and y data


    def fit_landau_migrad(x, y, p0, limit_mpv, limit_eta, limit_sigma, limit_A):
        #TODO make it possible with error calculation

        def minimizeMe(mpv, eta, sigma, A):
            chi2 = np.sum(np.square(y - langau(x, mpv, eta, sigma, A).astype(float)) / np.square(yerr.astype(float)))
            return chi2 / (x.shape[0] - 5)  # devide by NDF

        # Prefit to get correct errors
        yerr = np.sqrt(y)  # Assume error from measured data
        yerr[y < 1] = 1
        m = iminuit.Minuit(minimizeMe,
                           mpv=p0[0],
                           limit_mpv=limit_mpv,
                           error_mpv=1,
                           eta=p0[1],
                           error_eta=0.1,
                           limit_eta=limit_eta,
                           sigma=p0[2],
                           error_sigma=0.1,
                           limit_sigma=limit_sigma,
                           A=p0[3],
                           error_A=1,
                           limit_A=limit_A,
                           errordef=1,
                           print_level=2)
        m.migrad()

        if not m.get_fmin().is_valid:
            raise RuntimeError('Fit did not converge')

        # Main fit with model errors
        yerr = np.sqrt(langau(x,
                              mpv=m.values['mpv'],
                              eta=m.values['eta'],
                              sigma=m.values['sigma'],
                              A=m.values['A']))  # Assume error from measured data
        yerr[y < 1] = 1

        m = iminuit.Minuit(minimizeMe,
                           mpv=m.values['mpv'],
                           limit_mpv=limit_mpv,
                           error_mpv=1,
                           eta=m.values['eta'],
                           error_eta=0.1,
                           limit_eta=limit_eta,
                           sigma=m.values['sigma'],
                           error_sigma=0.1,
                           limit_sigma=limit_sigma,
                           A=m.values['A'],
                           error_A=1,
                           limit_A=limit_A,
                           errordef=1,
                           print_level=2)
        m.migrad()

        fit_values = m.values

        values = np.array([fit_values['mpv'],
                           fit_values['eta'],
                           fit_values['sigma'],
                           fit_values['A']])

        m.hesse()

        m.minos()
        minos_errors = m.get_merrors()

        if not minos_errors['mpv'].is_valid:
            print('Warning: MPV error determination with Minos failed! You can still use Hesse errors.')

        errors = np.array([(minos_errors['mpv'].lower, minos_errors['mpv'].upper),
                           (minos_errors['eta'].lower, minos_errors['eta'].upper),
                           (minos_errors['sigma'].lower, minos_errors['sigma'].upper),
                           (minos_errors['A'].lower, minos_errors['A'].upper)])

        return values, errors, m


    def fit_langau(self, x, ind_xmin = 0, bins = 500):
        """Fits the langau to data"""
        hist, edges = np.histogram(x, bins=bins)
        mpv, eta, sigma, A = 50000, 5, 4, 800

        # Fit with constrains
        coeff, pcov = curve_fit(pylandau.langau, edges[ind_xmin:-1], hist[ind_xmin:], absolute_sigma=True, p0=(mpv, eta, sigma, A), bounds=(1, 70000))

        return coeff, pcov, hist

    def get_clusters(self, data, num_cluster=1):
        """
        Get all clusters which seem important
        :param max_cluster: number of cluster which should be considered 1 is default and minimum. 0 makes no sense
        :return: list of data indizes after cluster consideration (so basically eventnumbers which are good)
        """
        return np.nonzero(data["Numclus"] == num_cluster) # Indizes of events with the desired clustersize

    def plot(self):
        """Plots the data calculated so the energy data and the langau"""

        for file, data in self.results_dict.items():
            fig = plt.figure("Langau from file: {!s}".format(file))

            # Plot delay
            plot = fig.add_subplot(111)
            n, bins, patches = plot.hist(data["signal"], bins=500, density=False, alpha=0.4, color="b")
            plot.plot(data["langau_data"][0], data["langau_data"][1], "r--", color="g")
            plot.set_xlabel('electrons [#]')
            plot.set_ylabel('Count [#]')
            plot.set_title('Energy deposition SR-90')

            fig.tight_layout()
            plt.draw()




if __name__ == "__main__":
    noise = noise_analysis(path = r"\\HEROS\dbloech\Alibava_measurements\VC811929\Pedestal.hdf5")