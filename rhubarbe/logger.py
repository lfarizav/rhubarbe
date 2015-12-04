import logging
import logging.config

# we essentially need
# * one all-purpose logger that goes into ./rhubarbe.log
# * one special logger for monitor that goes into /var/log/monitor.log

rhubarbe_logging_config = {
    'version' : 1,
    'disable_existing_loggers' : True,
    'formatters': { 
        'standard': { 
            'format': '%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s',
            'datefmt': '%m-%d %H:%M:%S'
        },
    },
    'handlers': {
        'rhubarbe': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'formatter': 'standard',
            'filename' : 'rhubarbe.log',
        },
        'monitor': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'formatter': 'standard',
            'filename' : '/var/log/monitor.log',
        },
    },
    'loggers': {
        'monitor': {
            'handlers': ['monitor'],
            'level': 'INFO',
            'propagate': False,
        },
        'rhubarbe': {
            'handlers': ['rhubarbe'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

logging.config.dictConfig(rhubarbe_logging_config)

# general case:
# from rhubarbe.logger import logger
logger = logging.getLogger('rhubarbe')

# monitor
# from rhubarbe.logger import monitor_logger as logger
monitor_logger = logging.getLogger('monitor')

#################### test
if __name__ == '__main__':
    logger.info("in rhubarbe")
    monitor_logger.info("in monitor")

    
