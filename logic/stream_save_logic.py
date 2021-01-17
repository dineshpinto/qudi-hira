# -*- coding: utf-8 -*-
"""
This module handles the stream saving of data.

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

import datetime
import inspect
import logging
import os
import sys
import time

import numpy as np

from core.configoption import ConfigOption
from core.util.mutex import Mutex
from core.util.network import netobtain
from logic.save_logic import SaveLogic, DailyLogHandler


class StreamSaveLogic(SaveLogic):
    """
    A general class which saves all kinds of data in a general sense.

    Example config for copy-paste:

    savelogic:
        module.Class: 'save_logic.SaveLogic'
        win_data_directory: 'C:/Data'   # DO NOT CHANGE THE DIRECTORY HERE! ONLY IN THE CUSTOM FILE!
        unix_data_directory: 'Data/'
        log_into_daily_directory: True
        save_pdf: True
        save_png: True
    """

    _win_data_dir = ConfigOption('win_data_directory', 'C:/Data/')
    _unix_data_dir = ConfigOption('unix_data_directory', 'Data')
    log_into_daily_directory = ConfigOption('log_into_daily_directory', False, missing='warn')

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        # locking for thread safety
        self.lock = Mutex()

        # name of active POI, default to empty string
        self.active_poi_name = ''

        # Some default variables concerning the operating system:
        self.os_system = None

        # Chech which operation system is used and include a case if the
        # directory was not found in the config:
        if sys.platform in ('linux', 'darwin'):
            self.os_system = 'unix'
            self.data_dir = self._unix_data_dir
        elif 'win32' in sys.platform or 'AMD64' in sys.platform:
            self.os_system = 'win'
            self.data_dir = self._win_data_dir
        else:
            raise Exception('Identify the operating system.')

        # Expand environment variables in the data_dir path (e.g. $HOME)
        self.data_dir = os.path.expandvars(self.data_dir)

        # start logging into daily directory?
        if not isinstance(self.log_into_daily_directory, bool):
            self.log.warning(
                'log entry in configuration is not a '
                'boolean. Falling back to default setting: False.')
            self.log_into_daily_directory = False

        self._daily_loghandler = None

    def on_activate(self):
        """
        Definition, configuration and initialisation of the SaveLogic.
        """
        if self.log_into_daily_directory:
            # adds a log handler for logging into daily directory
            self._daily_loghandler = DailyLogHandler(
                '%Y%m%d-%Hh%Mm%Ss-qudi.log', self)
            self._daily_loghandler.setFormatter(logging.Formatter(
                '%(asctime)s %(name)s %(levelname)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'))
            self._daily_loghandler.setLevel(logging.DEBUG)
            logging.getLogger().addHandler(self._daily_loghandler)
        else:
            self._daily_loghandler = None

    def on_deactivate(self):
        if self._daily_loghandler is not None:
            # removes the log handler logging into the daily directory
            logging.getLogger().removeHandler(self._daily_loghandler)

    @property
    def dailylog(self):
        """
        Returns the daily log handler.
        """
        return self._daily_loghandler

    def dailylog_set_level(self, level):
        """
        Sets the log level of the daily log handler

        @param level int: log level, see logging
        """
        self._daily_loghandler.setLevel(level)

    def create_file_and_header(self, data, filepath=None, parameters=None, filename=None, filelabel=None,
                  timestamp=None, filetype='text', fmt='%.15e', delimiter='\t', plotfig=None):
        """
        General save routine for data.

        @param dictionary data: Dictionary containing the data to be saved. The keys should be
                                strings containing the data header/description. The corresponding
                                items are one or more 1D arrays or one 2D array containing the data
                                (list or numpy.ndarray). Example:

                                    data = {'Frequency (MHz)': [1,2,4,5,6]}
                                    data = {'Frequency': [1, 2, 4], 'Counts': [234, 894, 743, 423]}
                                    data = {'Frequency (MHz),Counts':[[1,234], [2,894],...[30,504]]}

        @param string filepath: optional, the path to the directory, where the data will be saved.
                                If the specified path does not exist yet, the saving routine will
                                try to create it.
                                If no path is passed (default filepath=None) the saving routine will
                                create a directory by the name of the calling module inside the
                                daily data directory.
                                If no calling module can be inferred and/or the requested path can
                                not be created the data will be saved in a subfolder of the daily
                                data directory called UNSPECIFIED
        @param dictionary parameters: optional, a dictionary with all parameters you want to save in
                                      the header of the created file.
        @parem string filename: optional, if you really want to fix your own filename. If passed,
                                the whole file will have the name

                                    <filename>

                                If nothing is specified the save logic will generate a filename
                                either based on the module name from which this method was called,
                                or it will use the passed filelabel if that is speficied.
                                You also need to specify the ending of the filename!
        @parem string filelabel: optional, if filelabel is set and no filename was specified, the
                                 savelogic will create a name which looks like

                                     YYYY-MM-DD_HHh-MMm-SSs_<filelabel>.dat

                                 The timestamp will be created at runtime if no user defined
                                 timestamp was passed.
        @param datetime timestamp: optional, a datetime.datetime object. You can create this object
                                   with datetime.datetime.now() in the calling module if you want to
                                   fix the timestamp for the filename. Be careful when passing a
                                   filename and a timestamp, because then the timestamp will be
                                   ignored.
        @param string filetype: optional, the file format the data should be saved in. Valid inputs
                                are 'text', 'xml' and 'npz'. Default is 'text'.
        @param string or list of strings fmt: optional, format specifier for saved data. See python
                                              documentation for
                                              "Format Specification Mini-Language". If you want for
                                              example save a float in scientific notation with 6
                                              decimals this would look like '%.6e'. For saving
                                              integers you could use '%d', '%s' for strings.
                                              The default is '%.15e' for numbers and '%s' for str.
                                              If len(data) > 1 you should pass a list of format
                                              specifiers; one for each item in the data dict. If
                                              only one specifier is passed but the data arrays have
                                              different data types this can lead to strange
                                              behaviour or failure to save right away.
        @param string delimiter: optional, insert here the delimiter, like '\n' for new line, '\t'
                                 for tab, ',' for a comma ect.

        1D data
        =======
        1D data should be passed in a dictionary where the data trace should be assigned to one
        identifier like

            {'<identifier>':[list of values]}
            {'Numbers of counts':[1.4, 4.2, 5, 2.0, 5.9 , ... , 9.5, 6.4]}

        You can also pass as much 1D arrays as you want:

            {'Frequency (MHz)':list1, 'signal':list2, 'correlations': list3, ...}

        2D data
        =======
        2D data should be passed in a dictionary where the matrix like data should be assigned to
        one identifier like

            {'<identifier>':[[1,2,3],[4,5,6],[7,8,9]]}

        which will result in:
            <identifier>
            1   2   3
            4   5   6
            7   8   9


        YOU ARE RESPONSIBLE FOR THE IDENTIFIER! DO NOT FORGET THE UNITS FOR THE SAVED TIME
        TRACE/MATRIX.
        """
        start_time = time.time()
        # Create timestamp if none is present
        if timestamp is None:
            timestamp = datetime.datetime.now()

        # try to trace back the functioncall to the class which was calling it.
        try:
            frm = inspect.stack()[1]
            # this will get the object, which called the save_data function.
            mod = inspect.getmodule(frm[0])
            # that will extract the name of the class.
            module_name = mod.__name__.split('.')[-1]
        except:
            # Sometimes it is not possible to get the object which called the save_data function
            # (such as when calling this from the console).
            module_name = 'UNSPECIFIED'

        # determine proper file path
        if filepath is None:
            self.filepath = self.get_path_for_module(module_name)
        elif not os.path.exists(filepath):
            os.makedirs(filepath)
            self.log.info('Custom filepath does not exist. Created directory "{0}"'
                          ''.format(filepath))

        # create filelabel if none has been passed
        if filelabel is None:
            filelabel = module_name
        if self.active_poi_name != '':
            filelabel = self.active_poi_name.replace(' ', '_') + '_' + filelabel

        # determine proper unique filename to save if none has been passed
        if filename is None:
            self.filename = timestamp.strftime('%Y%m%d-%H%M-%S' + '_' + filelabel + '.dat')

        # Check format specifier.
        if not isinstance(fmt, str) and len(fmt) != len(data):
            self.log.error('Length of list of format specifiers and number of data items differs. '
                           'Saving not possible. Please pass exactly as many format specifiers as '
                           'data arrays.')
            return -1

        # Create header string for the file
        header = 'Saved Data from the class {0} on {1}.\n' \
                 ''.format(module_name, timestamp.strftime('%d.%m.%Y at %Hh%Mm%Ss'))
        header += '\nParameters:\n===========\n\n'
        # Include the active POI name (if not empty) as a parameter in the header
        if self.active_poi_name != '':
            header += 'Measured at POI: {0}\n'.format(self.active_poi_name)
        # add the parameters if specified:
        if parameters is not None:
            # check whether the format for the parameters have a dict type:
            if isinstance(parameters, dict):
                if isinstance(self._additional_parameters, dict):
                    parameters = {**self._additional_parameters, **parameters}
                for entry, param in parameters.items():
                    if isinstance(param, float):
                        header += '{0}: {1:.16e}\n'.format(entry, param)
                    else:
                        header += '{0}: {1}\n'.format(entry, param)
            # make a hardcore string conversion and try to save the parameters directly:
            else:
                self.log.error('The parameters are not passed as a dictionary! The SaveLogic will '
                               'try to save the parameters nevertheless.')
                header += 'not specified parameters: {0}\n'.format(parameters)
        header += '\nData:\n=====\n'

    def write_data(self, data, fmt='%.15e', filetype='text', delimiter='\t'):
        # write data to file
        # FIXME: Implement other file formats
        # write to textfile
        

        # Try to cast data array into numpy.ndarray if it is not already one
        # Also collect information on arrays in the process and do sanity checks
        found_1d = False
        found_2d = False
        multiple_dtypes = False
        arr_length = []
        arr_dtype = []
        max_row_num = 0
        max_line_num = 0
        for keyname in data:
            # Cast into numpy array
            if not isinstance(data[keyname], np.ndarray):
                try:
                    data[keyname] = np.array(data[keyname])
                except:
                    self.log.error('Casting data array of type "{0}" into numpy.ndarray failed. '
                                   'Could not save data.'.format(type(data[keyname])))
                    return -1

            # determine dimensions
            if data[keyname].ndim < 3:
                length = data[keyname].shape[0]
                arr_length.append(length)
                if length > max_line_num:
                    max_line_num = length
                if data[keyname].ndim == 2:
                    found_2d = True
                    width = data[keyname].shape[1]
                    if max_row_num < width:
                        max_row_num = width
                else:
                    found_1d = True
                    max_row_num += 1
            else:
                self.log.error('Found data array with dimension >2. Unable to save data.')
                return -1

            # determine array data types
            if len(arr_dtype) > 0:
                if arr_dtype[-1] != data[keyname].dtype:
                    multiple_dtypes = True
            arr_dtype.append(data[keyname].dtype)

        # Raise error if data contains a mixture of 1D and 2D arrays
        if found_2d and found_1d:
            self.log.error('Passed data dictionary contains 1D AND 2D arrays. This is not allowed. '
                           'Either fit all data arrays into a single 2D array or pass multiple 1D '
                           'arrays only. Saving data failed!')
            return -1
        
        if filetype == 'text':
            # Reshape data if multiple 1D arrays have been passed to this method.
            # If a 2D array has been passed, reformat the specifier
            if len(data) != 1:
                identifier_str = ''
                if multiple_dtypes:
                    field_dtypes = list(zip(['f{0:d}'.format(i) for i in range(len(arr_dtype))],
                                            arr_dtype))
                    new_array = np.empty(max_line_num, dtype=field_dtypes)
                    for i, keyname in enumerate(data):
                        identifier_str += keyname + delimiter
                        field = 'f{0:d}'.format(i)
                        length = data[keyname].size
                        new_array[field][:length] = data[keyname]
                        if length < max_line_num:
                            if isinstance(data[keyname][0], str):
                                new_array[field][length:] = 'nan'
                            else:
                                new_array[field][length:] = np.nan
                else:
                    new_array = np.empty([max_line_num, max_row_num], arr_dtype[0])
                    for i, keyname in enumerate(data):
                        identifier_str += keyname + delimiter
                        length = data[keyname].size
                        new_array[:length, i] = data[keyname]
                        if length < max_line_num:
                            if isinstance(data[keyname][0], str):
                                new_array[length:, i] = 'nan'
                            else:
                                new_array[length:, i] = np.nan
                # discard old data array and use new one
                data = {identifier_str: new_array}
            elif found_2d:
                keyname = list(data.keys())[0]
                identifier_str = keyname.replace(', ', delimiter).replace(',', delimiter)
                data[identifier_str] = data.pop(keyname)
            else:
                identifier_str = list(data)[0]
            header = list(data)[0]
            self.save_array_as_text(data=data[identifier_str], filename=self.filename, filepath=self.filepath,
                                    fmt=fmt, header=header, delimiter=delimiter, comments='#',
                                    append=True)
        # write npz file and save parameters in textfile
        elif filetype == 'npz':
            header = str(list(data.keys()))[1:-1]
            np.savez_compressed(self.filepath + '/' + self.filename[:-4], **data)
            self.save_array_as_text(data=[], filename=self.filename[:-4] + '_params.dat', filepath=self.filepath,
                                    fmt=fmt, header=header, delimiter=delimiter, comments='#',
                                    append=False)
        else:
            self.log.error('Only saving of data as textfile and npz-file is implemented. Filetype "{0}" is not '
                           'supported yet. Saving as textfile.'.format(filetype))
            self.save_array_as_text(data=data[identifier_str], filename=filename, filepath=filepath,
                                    fmt=fmt, header=header, delimiter=delimiter, comments='#',
                                    append=False)

    def save_array_as_text(self, data, filename, filepath='', fmt='%.15e', header='',
                           delimiter='\t', comments='#', append=False):
        """
        An Independent method, which can save a 1D or 2D numpy.ndarray as textfile.
        Can append to files.
        """
        # write to file. Append if requested.
        if append:
            with open(os.path.join(filepath, filename), 'ab') as file:
                np.savetxt(file, data, fmt=fmt, delimiter=delimiter, header=header,
                           comments=comments)
        else:
            with open(os.path.join(filepath, filename), 'wb') as file:
                np.savetxt(file, data, fmt=fmt, delimiter=delimiter, header=header,
                           comments=comments)
        return

    def get_daily_directory(self):
        """ Gets or creates daily save directory.

          @return string: path to the daily directory.

        If the daily directory does not exits in the specified <root_dir> path
        in the config file, then it is created according to the following scheme:

            <root_dir>\<year>\<month>\<yearmonthday>

        and the filepath is returned. There should be always a filepath
        returned.
        """
        current_dir = os.path.join(
            self.data_dir,
            time.strftime("%Y"),
            time.strftime("%m"),
            time.strftime("%Y%m%d"))

        if not os.path.isdir(current_dir):
            self.log.info("Creating directory for today's data:\n"
                          '{0}'.format(current_dir))

            # The exist_ok=True is necessary here to prevent Error 17 "File Exists"
            # Details at http://stackoverflow.com/questions/12468022/python-fileexists-error-when-making-directory
            os.makedirs(current_dir, exist_ok=True)

        return current_dir

    def get_path_for_module(self, module_name):
        """
        Method that creates a path for 'module_name' where data are stored.

        @param string module_name: Specify the folder, which should be created in the daily
                                   directory. The module_name can be e.g. 'Confocal'.
        @return string: absolute path to the module name
        """
        dir_path = os.path.join(self.get_daily_directory(), module_name)

        if not os.path.isdir(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        return dir_path

    def get_additional_parameters(self):
        """ Method that return the additional parameters dictionary securely """
        return self._additional_parameters.copy()

    def update_additional_parameters(self, *args, **kwargs):
        """
        Method to update one or multiple additional parameters

        @param dict args: Optional single positional argument holding parameters in a dict to
                          update additional parameters from.
        @param kwargs: Optional keyword arguments to be added to additional parameters
        """
        if len(args) == 0:
            param_dict = kwargs
        elif len(args) == 1 and isinstance(args[0], dict):
            param_dict = args[0]
            param_dict.update(kwargs)
        else:
            raise TypeError('"update_additional_parameters" takes exactly 0 or 1 positional '
                            'argument of type dict.')

        for key in param_dict.keys():
            param_dict[key] = netobtain(param_dict[key])
        self._additional_parameters.update(param_dict)
        return

    def remove_additional_parameter(self, key):
        """
        remove parameter from additional parameters

        @param str key: The additional parameters key/name to delete
        """
        self._additional_parameters.pop(key, None)
        return
