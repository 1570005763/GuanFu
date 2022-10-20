libjsoncpp: 
	git clone https://github.com/open-source-parsers/jsoncpp.git -4 
	cd jsoncpp && mkdir -p build/debug
	mkdir share
	cd jsoncpp/build/debug/ && cmake -DCMAKE_BUILD_TYPE=debug -DBUILD_SHARED_LIBS=OFF -DCMAKE_ARCHIVE_OUTPUT_DIRECTORY=../../.. -G "Unix Makefiles" ../../   && make
	mkdir -p include/json
	cp -r jsoncpp/include/json/ include/
	rm -rf jsoncpp

start:
	if [ -e  share/start.sh ]; then rm share/start.sh; fi
	echo "echo -e  \"\e[1;31m welcome to Guanfu, this project will help you rebproduce a package ! \e[0m  \" " >> share/start.sh
	echo "cd /home/rebuild" >> share/start.sh
	echo "./json" >> share/start.sh
	echo "/bin/bash" >> share/start.sh

json:jsonCppUsage.cpp libjsoncpp start
	g++ jsonCppUsage.cpp libjsoncpp.a -I include -o json
	mv json share/json
	
clean:
	rm libjsoncpp.a 
	rm -r include
	rm -r share