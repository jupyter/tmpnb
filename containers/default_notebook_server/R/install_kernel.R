install.packages('RCurl')
install.packages('devtools')

library(devtools)

install_github('armstrtw/rzmq#8')
install_github("takluyver/IRdisplay")
install_github("takluyver/IRkernel")

IRkernel::installspec()
