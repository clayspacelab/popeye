"""

Base-classes for poulation encoding models and fits.


"""
from popeye.onetime import auto_attr
import time, ctypes, itertools
import pickle
import sharedmem
from scipy.stats import linregress, zscore
import popeye.utilities_md as utils
import numpy as np
import numexpr as ne
from scipy.optimize import NonlinearConstraint
from inspect import signature
from scipy.signal import detrend

try:  # pragma: no cover
    from types import SliceType
except ImportError:  # pragma: no cover
    SliceType = slice

def set_verbose(verbose):
    
    r"""A convenience function for setting the verbosity of a popeye model/fit.
    
    Paramaters
    ----------
    
    verbose : int
        0 = silent
        1 = print the final solution of an error-minimization
        2 = print each error-minimization step
        
    """
    
    # set verbose
    if verbose == 0: # pragma: no cover
        verbose = False
        very_verbose = False
    if verbose == 1: # pragma: no cover
        verbose = True
        very_verbose = False
    if verbose == 2: # pragma: no cover
        verbose = True
        very_verbose = True
    
    return verbose, very_verbose

class PopulationModel(object):
    
    r""" Base class for all pRF models."""
    
    def __init__(self, stimulus, hrf_model, normalizer=zscore, cached_model_path=None, 
                 nuisance=None):
        
        r"""Base class for all pRF models.
        
        Paramaters
        ----------
        
        stimulus : `StimulusModel` class instance
            An instance of the `StimulusModel` class containing the `stim_arr` and 
            various stimulus parameters, and can represent various stimulus modalities, 
            including visual, auditory, etc.
        
        hrf_model : callable
            A function that generates an HRF model given an HRF delay.
            For more information, see `popeye.utilties.double_gamma_hrf_hrf
            and `popeye.utilities.spm_hrf`
            
        nuisance : ndarray
            A nuisance regressor for removing effects of non-interest.
            You can regress out any nuisance effects from you data prior to fitting
            the model of interest. The nuisance model is a statsmodels.OLS compatible
            design matrix, and the user is expected to have already added any constants.
        
        """
        
        # propogate
        self.stimulus = stimulus
        self.hrf_model = hrf_model
        self.normalizer = normalizer
        self.nuisance = nuisance
        self.cached_model_path = cached_model_path
        
        # set up cached model if specified
        if self.cached_model_path is not None: # pragma: no cover
            self.resurrect_cached_model

    def remove_trend(signal, method='all'):
        if method == 'demean':
            return (signal - np.mean(signal, axis=-1)[..., None]) / np.mean(signal, axis=-1)[..., None]
        elif method == 'pct_signal_change':
            signal_mean = np.mean(signal, axis=-1)[..., None]
            signal_detrend = detrend(signal, axis=-1, type='linear') + signal_mean
            signal_pct = utils.percent_change(signal_detrend, ax=-1)
            return signal_pct
            
    def generate_ballpark_prediction(self): # pragma: no cover
        raise NotImplementedError("Each pRF model must implement its own ballpark prediction!") 
    
    def generate_prediction(self): # pragma: no cover
        raise NotImplementedError("Each pRF model must implement its own prediction!")
    
    def regress(self, X, y):
        slope, intercept = linregress(X, y)[0:2]
        if hasattr(self, 'bounded_amplitude') and self.bounded_amplitude:
            return np.abs(slope), intercept
        else:
            return slope, intercept
    
    def hrf(self):
        if hasattr(self, 'hrf_delay'): # pragma: no cover
            return self.hrf_model(self.hrf_delay, self.stimulus.tr_length)
        else: # pragma: no cover
            raise NotImplementedError("You must set the HRF delay to generate the HRF")
    
    def cache_model(self, grids, ncpus=1, Ns=None, verbose=False):
        
        # get parameter space
        if isinstance(grids[0], SliceType):
            params = [list(np.arange(g.start,g.stop,g.step)) for g in grids]
        else:
            params = [list(np.linspace(g[0],g[1],Ns)) for g in grids]
        
        # make combos
        combos = np.array([c for c in itertools.product(*params)])
        
        # sort them by rand
        idx = np.argsort(np.random.rand(combos.shape[0]))
        combos = combos[idx]
        
        def mini_predictor(combo): # pragma: no cover
            print('%s' %(np.round(combo,2)))
            combo_long = list(combo)
            combo_long.extend((1,0))
            self.data = self.generate_prediction(*combo_long)
            return self.generate_ballpark_prediction(*combo), combo
        
        # compute predictions
        with sharedmem.Pool(np=ncpus) as pool:
            models = pool.map(mini_predictor, combos)
        
        # clean up
        models = [m for m in models if not np.isnan(np.sum(m[0]))]
        
        # turn into array
        return models
    
    @auto_attr
    def resurrect_cached_model(self):
        dat = pickle.load(open(self.cached_model_path, 'rb'))
        timeseries = utils.generate_shared_array(np.array([d[0] for d in dat]), np.double)
        parameters = utils.generate_shared_array(np.array([d[1] for d in dat]), np.double)
        return timeseries, parameters
    
    @auto_attr
    def cached_model_timeseries(self): # pragma: no cover
        return self.resurrect_cached_model[0]
    
    @auto_attr
    def cached_model_parameters(self): # pragma: no cover
        return self.resurrect_cached_model[1]
        
        
class PopulationFit(object):
    
    
    r""" Base class for all pRF model fits."""
    
    
    def __init__(self, model, data, grids, bounds, 
                 voxel_index=(1,2,3), Ns=None, auto_fit=True, grid_only=False,
                 fit_method = '2step', outer_limit=2, nuisance=utils.detrend_psc, verbose=False):
        
        r"""A class containing tools for fitting pRF models.
        
        The `PopulationFit` class houses all the fitting tool that are associated with 
        estimatinga pRF model.  The `PopulationFit` takes a `PoulationModel` instance 
        `model` and a time-series `data`.  In addition, extent and sampling-rate of a 
        brute-force grid-search is set with `grids` and `Ns`.  Use `bounds` to set 
        limits on the search space for each parameter.  
        
        Paramaters
        ----------
        
        model : `PopulationModel` class instance
            An object representing pRF model.
        
        data : ndarray
            An array containing the measured BOLD signal of a single voxel.
        
        grids : tuple
            A tuple indicating the search space for the brute-force grid-search.
            The tuple contains pairs of upper and lower bounds for exploring a
            given dimension.  For example `grids=((-10,10),(0,5),)` will
            search the first dimension from -10 to 10 and the second from 0 to 5.
            These values cannot be `None`. 
            
            For more information, see `scipy.optimize.brute`.
        
        bounds : tuple
            A tuple containing the upper and lower bounds for each parameter
            in `parameters`.  If a parameter is not bounded, simply use
            `None`.  For example, `fit_bounds=((0,None),(-10,10),)` would 
            bound the first parameter to be any positive number while the
            second parameter would be bounded between -10 and 10.
        
        voxel_index : tuple
            A tuple containing the index of the voxel being modeled. The 
            fitting procedure does not require a voxel index, but 
            collating the results across many voxels will does require voxel
            indices. With voxel indices, the brain volume can be reconstructed 
            using the newly computed model estimates.
        
        Ns : int
            Number of samples per stimulus dimension to sample during the ballpark search.
            
            For more information, see `scipy.optimize.brute`.
            
            Tnis option can be ignored if you want to specify your own sampling rate 
            for a given parameter. For more information, see `popeye.utilities.grid_slice`.
        
        auto_fit : bool
            A flag for automatically running the fitting procedures once the 
            `GaussianFit` object is instantiated.
            
        outer_limit : float
            Used to constrain prf positions such they cannot be more than 
            outer_limit*sigma away from the outer edge of the stimulus display
        
        verbose : int
            0 = silent
            1 = print the final solution of an error-minimization
            2 = print each error-minimization step
        
        """
        
        # absorb vars
        self.grids = grids
        self.bounds = bounds
        self.Ns = Ns
        self.voxel_index = voxel_index
        self.auto_fit = auto_fit
        self.grid_only = grid_only #leaving this flag in for backward compatibility
        self.outer_limit = outer_limit
        self.model = model
        self.data = data
        self.nuisance = nuisance
        self.verbose, self.very_verbose = set_verbose(verbose)

        # Remove trend from the data

        
        assert fit_method in ['2step','grid_only','global_opt'], \
            'Invalid fit method: must be one of "2step", "grid_only" "global_opt"'
        
        #override fit_method if grid_onlny specified    
        self.fit_method = 'grid_only' if self.grid_only else fit_method
       
        
        #We're fitting x,y positions, but we want to fit in a circular region to
        #avoid numerical issues, among other reasons, so set constraints to be used
        #by solvers
        self.constraints = (
                NonlinearConstraint(lambda x: np.sqrt(x[0]**2 + x[1]**2), 
                                    -np.inf, self.model.stimulus.screen_dva),
                NonlinearConstraint(lambda x: np.sqrt(x[0]**2 + x[1]**2) - self.outer_limit*x[2], 
                                    -np.inf, self.model.stimulus.screen_dva/2)
            )
        
        
        # regress out any 
        if self.nuisance is not None:
            self.data = self.nuisance(self.data)
            
        # if self.model.nuisance is not None: # pragma: no cover
            # self.model.nuisance_model = sm.OLS(self.data,self.model.nuisance)
            # self.model.results = self.model.nuisance_model.fit()
            # self.original_data = self.data
            # self.data = self.model.results.resid
        
        # push data down to model
        # there is probably a better way of exposing the data
        # to the PopulationModel. the idea is that the we want to
        # compute beta and baseline via linear regression rather
        # than estimate through optimization. Thus, model needs to see
        # the data. Since the data is in shared memory, no overhead incurred.
        self.model.data = data
        
        # if self.bounds[-2][0] is not None and self.bounds[-2][0] > 0:
        #     self.model.bounded_amplitude = True # +/- amplitudes
        # else:
        #     self.model.bounded_amplitude = False # + amplitudes
        
        # automatic fitting
        if self.auto_fit: # pragma: no cover
            
            # start
            self.start = time.time()
            
            # fit
            
            if fit_method != 'global_opt':
                self.ballpark
            if fit_method != 'grid_only':
                self.estimate
                self.overloaded_estimate
            
            # finish
            self.finish = time.time()
            
            # performance
            if fit_method != 'grid_only':
                self.rss
                self.rsquared
            
            # flush if not testing (do we need this???)
            if not hasattr(self.model, 'store_search_space'): # pragma: no cover
                self.gradient_descent = [None]*6
                self.brute_force = [None,]*4
                self.global_opt = [None,]*6 #needed???
            
            # print
            if self.verbose: # pragma: no cover
                print(self.msg)
    
    
    @auto_attr
    def best_cached_model_parameters(self): # pragma: no cover
        a = self.model.cached_model_timeseries
        b = self.data
        rss = ne.evaluate('sum((a-b)**2,axis=1)')
        idx = np.argmin(rss)
        return self.model.cached_model_parameters[idx]
    
    # the brute search
    @auto_attr
    def brute_force(self):
        return utils.brute_force_search(self.data,
                                        utils.error_function,
                                        self.model.generate_ballpark_prediction,
                                        self.grids,
                                        self.Ns,
                                        self.very_verbose)
    
    @auto_attr
    def ballpark(self):
        if self.fit_method=='global_opt':
            #this is hacky but fine for now as a way of getting the number of parameters in the model
            #better this than add conditionals everywhere...
            model_n = len(signature(self.model.generate_ballpark_prediction).parameters) + 2
            return np.tile(None,model_n)    
        
        else:
            if self.model.cached_model_path is not None: # pragma: no cover
                return self.best_cached_model_parameters
            else:
                return np.append(self.brute_force[0],(self.slope,self.intercept))
            
            
    # the gradient search
    @auto_attr
    def gradient_descent(self):
        
        if self.very_verbose: # pragma: no cover
            print('The gridfit solution was %s, starting gradient descent ...' %(self.ballpark))
         
        return utils.gradient_descent_search(self.data,
                                              utils.error_function,
                                              self.model.generate_prediction,
                                              self.ballpark,
                                              self.bounds,
                                              self.very_verbose,
                                              constraints=self.constraints)
    
    
    # the global optimization
    @auto_attr
    def global_opt(self,popsize=8,workers=-1,**kwargs):
        

        return utils.global_search(self.data,
                                              utils.error_function,
                                              self.model.generate_prediction,
                                              self.bounds,
                                              self.very_verbose,
                                              constraints=self.constraints,
                                              popsize=popsize,workers=workers,**kwargs)
        
    
    
    @auto_attr
    def overloaded_estimate(self): # pragma: no cover
        
        """
        `overloaded_estimate` allows for representing the fitted
        parameter estimates in more useful units, such as 
        Cartesian to polar coordinates, or Hz to log(Hz).
        
        """
        
        return None
    
    @auto_attr
    def estimate(self):
        
        """
        `overloaded_estimate` allows for flexible representaiton of the
        final model estimate. For instance, you may fit the model parameters
        in cartesian space but would rather represent the fit in polar
        coordinates. 
        
        """
        
        return self.global_opt.x if self.fit_method=='global_opt' else self.gradient_descent.x
    
    @auto_attr
    def ballpark_prediction(self):
        # return self.model.generate_prediction(*np.append(self.brute_force[0],(1,0)), unscaled=True)
        return self.model.generate_ballpark_prediction(*self.brute_force[0], self.data, unscaled=True)
    
    @auto_attr
    def scaled_ballpark_prediction(self):
        return self.ballpark_prediction * self.slope + self.intercept
    
    @auto_attr
    def slope(self):
        return self.model.regress(self.ballpark_prediction, self.data)[0]
    
    @auto_attr
    def intercept(self):
        return self.model.regress(self.ballpark_prediction, self.data)[1]
    
    @auto_attr
    def prediction(self):
        if self.fit_method=='grid_only':
            return self.model.generate_prediction(*self.ballpark)
        else:
            return self.model.generate_prediction(*self.estimate)
    
    # @auto_attr
    # def rsquared(self):
    #     return np.corrcoef(self.data, self.prediction)[1][0]**2
    
    @auto_attr
    def rsquared(self):
        #consider caching the rawrss after normalizing?
        r2 = 1 - (self.rss / np.sum((self.data - self.data.mean())**2))

        #clean up [adapted from vista rmGet.m]
        if np.isinf(r2) | np.isnan(r2):
            return 0
        
        r2 = np.maximum(r2, 0)
        r2 = np.minimum(r2, 1)
        
        return r2
    
    @auto_attr
    def rsquared_adj(self):
        return 1 - (1-self.rsquared)*(len(self.data)-1)/(len(self.data)-len(self.estimate)-1)
    
    @auto_attr
    def rsquared0(self):
        return np.corrcoef(self.data, self.scaled_ballpark_prediction)[1][0]**2 if self.fit_method!='global_opt' else None
    
    @auto_attr
    def rss(self):
        return np.sum((self.data - self.prediction)**2)
    
    @auto_attr
    def msg(self):
        if self.auto_fit is True and self.overloaded_estimate is not None: # pragma: no cover
            txt = ("VOXEL=(%.03d,%.03d,%.03d)   TIMEMS=%.04d   RSQ=%.02f  EST=%s"
                %(self.voxel_index[0],
                  self.voxel_index[1],
                  self.voxel_index[2],
                  (self.finish-self.start)*1000,
                  self.rsquared,
                  np.round(self.overloaded_estimate,4)))
        elif self.auto_fit is True: # pragma: no cover
            txt = ("VOXEL=(%.03d,%.03d,%.03d)   TIMEMS=%.04d   RSQ=%.02f  EST=%s"
                %(self.voxel_index[0],
                  self.voxel_index[1],
                  self.voxel_index[2],
                  (self.finish-self.start)*1000,
                  self.rsquared,
                  np.round(self.estimate,4)))
        else: # pragma: no cover
            txt = ("VOXEL=(%.03d,%.03d,%.03d)   RSQ=%.02f  EST=%s" 
                %(self.voxel_index[0],
                  self.voxel_index[1],
                  self.voxel_index[2],
                  self.rsquared,
                  np.round(self.estimate,4)))
        return txt
            
class StimulusModel(object):

    def __init__(self, stim_arr, dtype=ctypes.c_int16, tr_length=1.0):
        
        r"""A base class for all encoding stimuli.
        
        This class houses the basic and common features of the encoding stimulus, which
        along with a `PopulationModel` constitutes what is commonly referred to as the 
        pRF model.
        
        Paramaters
        ----------
        
        stim_arr : ndarray
            An array containing the stimulus.  The dimensionality of the data is arbitrary
            but must be consistent with the pRF model, as specified in `PopulationModel` and 
            `PopluationFit` class instances.
        
        dtype : string
            Sets the data type the stimulus array is cast into.
        
        dtype : string
            Sets the data type the stimulus array is cast into.
        
        tr_length : float
            The repetition time (TR) in seconds.
            
        """
        
        self.dtype = dtype
        self.stim_arr = utils.generate_shared_array(stim_arr, self.dtype)
        self.tr_length = tr_length
