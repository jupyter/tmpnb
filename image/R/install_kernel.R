install.packages('RCurl')
install.packages('devtools')

library(devtools)

install_github('armstrtw/rzmq', pull=8, ref=NULL)
install_github("takluyver/IRdisplay")
install_github("takluyver/IRkernel")

IRkernel::installspec()
