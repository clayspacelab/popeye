"""

First pass at a stimulus model for abstracting the qualities and functionality of a stimulus
into an abstract class.  For now, we'll assume the stimulus model only pertains to visual 
stimuli on a visual display over time (i.e., 3D).  Hopefully this can be extended to other stimuli
with an arbitrary number of dimensions (e.g., auditory stimuli).

"""
from __future__ import division
import ctypes

import numpy as np
from pylab import specgram
from numpy.lib import stride_tricks
from scipy.misc import imresize
import nibabel

from popeye.base import StimulusModel
from popeye.onetime import auto_attr
import popeye.utilities as utils

def generate_spectrogram(signal, NFFT, Fs, noverlap):
    
    spectrogram, freqs, times, handle = specgram(signal,NFFT=NFFT,Fs=Fs,noverlap=noverlap);
    
    return spectrogram, freqs, times

class AuditoryStimulus(StimulusModel):
    
    """ Abstract class for stimulus model """
    
    
    def __init__(self, stim_arr, NFFT, Fs, noverlap, dtype, tr_length):
        
        # this is a weird notation
        StimulusModel.__init__(self, stim_arr, dtype, tr_length)
        
        # absorb the vars
        self.NFFT = NFFT
        self.Fs = Fs
        self.noverlap = noverlap
        
        # create the vars via matplotlib
        spectrogram, freqs, times = generate_spectrogram(self.stim_arr, self.NFFT, self.Fs, self.noverlap)
        
        # share them
        self.spectrogram = utils.generate_shared_array(spectrogram, ctypes.c_double)
        self.freqs = utils.generate_shared_array(freqs, ctypes.c_double)
        
        # # why don't the times returned from specgram start at 0? they are time bin centers?
        # self.target_times = utils.generate_shared_array(target_times, ctypes.c_double)