import os, sys, platform, time
from time import sleep
try:
    import requests
except:
    os.system('pip install requests')
def brand():
	os.system('clear')
	print("""
██████╗ ██████╗ ███████╗██╗  ██╗
██╔══██╗██╔══██╗██╔════╝╚██╗██╔╝
██║  ██║██████╔╝█████╗   ╚███╔╝ 
██║  ██║██╔══██╗██╔══╝   ██╔██╗ 
██████╔╝██║  ██║███████╗██╔╝ ██╗
╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
                                """)
	time.sleep(1)
	os.system('clear')
	print("""
██╗███████╗
██║██╔════╝
██║███████╗
██║╚════██║
██║███████║
╚═╝╚══════╝
           """)
	time.sleep(1)
	os.system('clear')
	print("""
██╗  ██╗██╗███╗   ██╗ ██████╗ 
██║ ██╔╝██║████╗  ██║██╔════╝ 
█████╔╝ ██║██╔██╗ ██║██║  ███╗
██╔═██╗ ██║██║╚██╗██║██║   ██║
██║  ██╗██║██║ ╚████║╚██████╔╝
╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝ ╚═════╝ 
                              """)
	time.sleep(1)
	os.system('clear')
brand()
bit = platform.architecture()[0]
if bit == '64bit':
    import Nokia64
elif bit == '32bit':
    import Nokia64
