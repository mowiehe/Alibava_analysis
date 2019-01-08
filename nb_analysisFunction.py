# This files contains analysis function optimizes by numba jit capabilities

import numpy as np
from numba import jit, prange
from tqdm import tqdm
import scipy


@jit(parallel = True, cache=False)
def parallel_event_processing(goodtiming, events, pedestal, meanCMN, meanCMsig, noise, numchan, SN_cut, SN_ratio, SN_cluster, max_clustersize = 5, masking=True, material=1):
    """Parallel processing of events.
     Did not show any performance improvements. Maybe a bug?"""
    goodevents = goodtiming[0].shape[0]
    prodata = np.zeros(goodevents, dtype=object)
    hitmap = np.zeros(numchan)
    automasked = 0
    for i in tqdm(prange(goodevents), desc="Events processed:"):
        #print(i)
        signal, SN, CMN, CMsig = nb_process_event(events[i], pedestal, meanCMN, meanCMsig, noise, numchan)
        channels_hit, clusters, numclus, clustersize, automasked_hits = nb_clustering(signal, SN, SN_cut,
                                                                                      SN_ratio, SN_cluster, numchan,
                                                                                      max_clustersize=max_clustersize,
                                                                                      masking=masking,
                                                                                      material=material)
        automasked += automasked_hits
        for channel in channels_hit:
            hitmap[channel] += 1

        prodata[i]=[
            signal,
            SN,
            CMN,
            CMsig,
            hitmap,
            channels_hit,
            clusters,
            numclus,
            clustersize]

    return prodata, automasked

@jit(parallel = False, nopython = False)
def nb_clustering(event_obj, SN, SN_cut, SN_ratio, SN_cluster, numchan, max_clustersize = 5, masking=True, material=1):
    """Looks for cluster in a event"""
    event = event_obj
    channels = np.nonzero(np.abs(SN) > SN_cut)[0]  # Only channels which have a signal/Noise higher then the signal/Noise cut
    automasked_hit = 0
    used_channels = np.ones(numchan)  # To keep track which channel have been used already here ones due to valid channel calculations
    numclus = 0  # The number of found clusters
    clusters_list = []
    clustersize = []
    strips = len(event)

    if masking:
        if material:
            # Todo: masking of dead channels etc.
            masked_ind = np.nonzero(np.take(event, channels) > 0)[0]  # So only negative values are considered
            valid_ind = np.nonzero(event < 0)[0]
            automasked_hit += len(masked_ind)
        else:
            masked_ind = np.nonzero(np.take(event, channels) < 0)[0]  # So only positive values are considered
            valid_ind = np.nonzero(event > 0)[0]
            automasked_hit += len(masked_ind)
    else:
        valid_ind = np.arange(strips)
        #masked_ind = np.zeros([-1])

    #masked_list = list(masked_ind)
    for i in valid_ind: # Define valid index to search for
        used_channels[i] = 0

    #Todo: race condition somewhere which leads to different results
    # Todo: misinterpretation of two very close clusters
    for ch in channels:  # Loop over all left channels which are a hit, here from "left" to "right"
            if not used_channels[ch]:# and ch not in masked_list:# and not masked_ind[ch]:  # Make sure we dont count everything twice
                used_channels[ch] = 1  # So now the channel is used
                cluster = [ch]  # Keep track of the individual clusters
                size = 1

                # Now make a loop to find neighbouring hits of cluster, we must go into both directions
                right_stop = 0
                left_stop = 0
                absSN = np.abs(SN)
                SNval = SN_cut * SN_ratio
                offset = int(max_clustersize * 0.5)
                for i in range(1, offset+1):  # Search plus minus the channel found Todo: first entry useless
                    if 0 < ch-i and ch+i < numchan:  # To exclude overrun
                        chp = ch+i
                        chm = ch-i
                        if absSN[chp] > SNval and not used_channels[chp] and not right_stop:
                            cluster.append(chp)
                            used_channels[chp] = 1
                            size += 1
                        elif absSN[chp] < SNval or used_channels[chp]:
                            right_stop = 1 # Prohibits search for to long clusters or already used channels

                        if absSN[chm] > SNval and not used_channels[chm] and not left_stop:
                            cluster.append(chm)
                            used_channels[chm] = 1
                            size += 1
                        elif absSN[chm] < SNval or used_channels[chm]:
                            left_stop = 1 # Prohibits search for to long clusters or already used channels
                # Look if the cluster SN is big enough to be counted as clusters
                SNcluster = np.sqrt(np.abs(np.sum(np.take(event, cluster))))
                if SNcluster > SN_cluster:
                    numclus = numclus+1
                    clusters_list.append(cluster)
                    clustersize.append(size)
                    #if numclus > 7:
                    #    print(event)
                    #    print(clusters_list)

    return channels, clusters_list, numclus, np.array(clustersize), automasked_hit

@jit(parallel=True, nopython = True, cache=False)
def nb_noise_calc(events, pedestal, numevents, numchannels):
    """Noise calculation, normal noise (NN) and common mode noise (CMN)
    Uses numba and numpy, this function uses jit for optimization"""
    score = np.zeros((numevents, numchannels), dtype=np.float64)  # Variable needed for noise calculations
    CMnoise = np.zeros(numevents, dtype=np.float64)
    CMsig = np.zeros(numevents, dtype=np.float64)

    for event in prange(numevents):  # Loop over all good events

        # Calculate the common mode noise for every channel
        cm = events[event][:] - pedestal  # Get the signal from event and subtract pedestal
        CMNsig = np.std(cm)  # Calculate the standard deviation
        CMN = np.mean(cm)  # Now calculate the mean from the cm to get the actual common mode noise

        # Calculate the noise of channels
        cn = cm - CMN  # Subtract the common mode noise --> Signal[arraylike] - pedestal[arraylike] - Common mode

        score[event] = cn
        # Append the common mode values per event into the data arrays
        CMnoise[event] = CMN
        CMsig[event] = CMNsig

    return score, CMnoise, CMsig  # Return everything

@jit(parallel=False, nopython = False, cache=True)
def nb_process_event(event, pedestal, meanCMN, meanCMsig, noise, numchan=256):
    """Processes single events"""

    # Calculate the common mode noise for every channel
    signal = event - pedestal  # Get the signal from event and subtract pedestal

    # Remove channels which have a signal higher then 5*CMsig+CMN which are not representative
    removed = np.nonzero(signal < (5 * meanCMsig + meanCMN))
    prosignal = np.take(signal, removed)  # Processed signal

    if prosignal.any():
        cmpro = np.mean(prosignal)
        sigpro = np.std(prosignal)

        corrsignal = signal - cmpro
        SN = corrsignal / noise

        return corrsignal, SN, cmpro, sigpro
    else:
        return np.zeros(numchan), np.zeros(numchan), 0, 0  # A default value return if everything fails


