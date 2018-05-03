# -*- coding: utf-8 -*-
"""
This file contains the Qudi logic for the extraction of laser pulses.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

import os
import importlib
import inspect

from qtpy import QtCore
from collections import OrderedDict
from core.module import StatusVar
from core.util.modules import get_main_dir
from logic.generic_logic import GenericLogic


class PulseExtractionLogic(GenericLogic):
    """

    """
    _modclass = 'PulseExtractionLogic'
    _modtype = 'logic'

    sigExtractionSettingsUpdated = QtCore.Signal(dict)

    # The currently chosen extraction method
    current_extraction_method = StatusVar(default='conv_deriv')

    # Parameters used by all or some extraction methods.
    # The keywords for the function arguments must be the same as these variable names.
    # If you define a new extraction method you can use two different kinds of parameters:
    # 1) The parameters defined in the __init__ of this module.
    #    These must be non-optional arguments.
    # 2) The StatusVars of this module. These parameters are optional arguments in your method
    #    definition with default values. If you need to define a new parameter, you must add it to
    #    these modules' StatusVars (with the same name as the argument keyword)
    # Make sure that you define static methods, i.e. do not make use of something like "self.<name>"
    # If you have properly defined your extraction method and added all parameters to this module
    # the PulsedMainGui should automatically generate the appropriate elements.
    conv_std_dev = StatusVar(default=20.0)
    count_threshold = StatusVar(default=10)
    min_laser_length = StatusVar(default=200e-9)
    threshold_tolerance = StatusVar(default=20e-9)

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        # Dictionaries holding references to the extraction methods
        self.gated_extraction_methods = None
        self.ungated_extraction_methods = None

        # ==========================================================================================
        # WARNING:
        # The variables declared below are not handled by the extraction_settings property.
        # They need to be set directly by a master qudi module. Only add additional parameters here
        # if they are needed in the controlling master module as well.
        # If you add something make sure to exclude the attribute name explicitly in the
        # extraction_settings property.
        # ==========================================================================================

        # Dictionary container holding information about the currently running sequence
        self.sampling_information = dict()
        # Dictionary container holding the fast counter settings
        self.fast_counter_settings = dict()
        # Dictionary container holding the measurement settings
        self.measurement_settings = dict()
        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self.gated_extraction_methods = OrderedDict()
        self.ungated_extraction_methods = OrderedDict()

        # Get all python modules to import from.
        # The assumption is that in the directory pulse_extraction_methods, there are
        # *.py files, which contain only methods!
        path = os.path.join(get_main_dir(), 'logic', 'pulse_extraction_methods')
        filename_list = [name[:-3] for name in os.listdir(path) if
                         os.path.isfile(os.path.join(path, name)) and name.endswith('.py')]

        for filename in filename_list:
            mod = importlib.import_module('logic.pulse_extraction_methods.{0}'.format(filename))
            for method in dir(mod):
                try:
                    # Check for callable function or method:
                    ref = getattr(mod, method)
                    if method.startswith(('gated_', 'ungated_')) and callable(ref) and (
                            inspect.ismethod(ref) or inspect.isfunction(ref)):
                        # Bind the method as an attribute to the Class
                        setattr(PulseExtractionLogic, method, staticmethod(ref))
                        # Add method to appropriate dictionary
                        if method.startswith('gated_'):
                            self.gated_extraction_methods[method[6:]] = getattr(self, method)
                        elif method.startswith('ungated_'):
                            self.ungated_extraction_methods[method[8:]] = getattr(self, method)
                except:
                    self.log.error('It was not possible to import element {0} from {1} into '
                                   'PulseExtractionLogic.'.format(method, filename))
        return

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        return

    @property
    def is_gated(self):
        return self.fast_counter_settings.get('is_gated')

    @property
    def extraction_settings(self):
        """
        This property holds all parameters needed for the currently selected extraction_method.

        @return dict:
        """
        # Get reference to the extraction method
        if self.is_gated:
            method = self.gated_extraction_methods.get(self.current_extraction_method)
        else:
            method = self.ungated_extraction_methods.get(self.current_extraction_method)
        # Get keyword arguments for the currently selected method
        settings_dict = self._get_extraction_method_kwargs(method)
        # Remove arguments that have a corresponding attribute defined in __init__
        for parameter in ('fast_counter_settings', 'sampling_information', 'measurement_settings'):
            if parameter in settings_dict:
                del settings_dict[parameter]
        # Attach current extraction method name
        settings_dict['method'] = self.current_extraction_method
        return settings_dict

    @extraction_settings.setter
    def extraction_settings(self, settings_dict):
        for name, value in settings_dict.items():
            if name == 'method':
                if (value in self.gated_extraction_methods and self.is_gated) or (
                        value in self.ungated_extraction_methods and not self.is_gated):
                    self.current_extraction_method = value
                else:
                    self.log.error('Extraction method "{0}" could not be found in '
                                   'PulseExtractionLogic.'.format(value))
                continue

            if not hasattr(self, name):
                self.log.warning('No extraction setting "{0}" found in PulseExtractionLogic.\n'
                                 'Creating it now but this can lead to problems.\nThis parameter '
                                 'is probably not part of any extraction method.'.format(name))
            if name not in ('count_data', 'fast_counter_settings', 'sampling_information',
                            'measurement_settings'):
                setattr(self, name, value)

        # emit signal with all important parameters for the currently selected analysis method
        self.sigExtractionSettingsUpdated.emit(self.extraction_settings)
        return

    def extract_laser_pulses(self, count_data):
        """

        @param count_data:
        @return:
        """
        if len(count_data.shape) > 1 and not self.is_gated:
            self.log.error('"is_gated" flag is set to False but the count data to extract laser '
                           'pulses from is in the format of a gated timetrace (2D numpy array).')
        elif len(count_data.shape) == 1 and self.is_gated:
            self.log.error('"is_gated" flag is set to True but the count data to extract laser '
                           'pulses from is in the format of an ungated timetrace (1D numpy array).')
        if self.is_gated:
            extraction_method = self.gated_extraction_methods[self.current_extraction_method]
        else:
            extraction_method = self.ungated_extraction_methods[self.current_extraction_method]
        kwargs = self._get_extraction_method_kwargs(extraction_method)
        return extraction_method(count_data, **kwargs)

    def _get_extraction_method_kwargs(self, method):
        """
        Get the proper values for keyword arguments other than "count_data" for <method> from this
        classes attributes.

        @param method: reference to a callable extraction method
        @return dict: A dictionary containing the argument keywords for <method> and corresponding
                      values from PulseExtractionLogic attributes.
        """
        # Sanity checking
        if not callable(method) or not (inspect.ismethod(method) or inspect.isfunction(method)):
            self.log.error('Method "_get_extraction_method_kwargs" needs a reference to a callable '
                           'method but instead received "{0}"'.format(type(method)))
            return dict()

        kwargs_dict = dict()
        method_signature = inspect.signature(method)
        for name in method_signature.parameters.keys():
            if name == 'count_data':
                pass
            elif hasattr(self, name):
                kwargs_dict[name] = getattr(self, name)
            else:
                kwargs_dict[name] = method_signature.parameters[name].default
                self.log.warning('Parameter "{0}" for extraction method "{1}" is no attribute of '
                                 'PulseExtractionLogic.\nTaking default value of "{2}" instead.'
                                 ''.format(name, method.__name__, kwargs_dict[name]))
        return kwargs_dict
