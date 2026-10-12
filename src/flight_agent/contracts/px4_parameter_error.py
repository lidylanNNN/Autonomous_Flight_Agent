'''Failure to read or validate a PX4 parameter.'''


class Px4ParameterReadError(RuntimeError):
    '''A PX4 parameter reading is unavailable or invalid.'''
