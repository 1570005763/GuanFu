// jsonCppUsage.cpp
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

#include "json/json.h"
// using namespace std;
using std::cerr;
using std::cout;
using std::endl;
using std::ifstream;
using std::ofstream;
using std::string;
using std::stringstream;

using std::map;
using std::vector;

map<string, string> map_property;
vector<string> vec_command;
vector<vector<string>> vec_binary;
map<string, string> map_dependency;
#define checksum_number 3

bool read_json_file() {
  ifstream read_file("./mount/old.guanfu");

  if (!read_file.is_open()) {
    cerr << "failure to open json file in ./mount/old.guanfu  !" << endl;
    return false;
  }

  Json::Reader reader;
  Json::Value frame;

  if (reader.parse(read_file, frame)) {
    map_property["version"] = frame["version"].asString();
    map_property["timestamp"] = frame["timestamp"].asString();
    map_property["name"] = frame["name"].asString();
    // map_property["binary_url"] = frame["binary"]["url"].asString();
    map_property["version_gf"] = frame["config"]["version_gf"].asString();
    map_property["architecture"] =
        frame["environment"]["architecture"].asString();
    map_property["kernel_version"] =
        frame["environment"]["kernel_version"].asString();
    map_property["sourceLink"] = frame["source"]["source_link"].asString();
    map_property["sourceType"] = frame["source"]["source_type"].asString();

    for (unsigned int i = 0; i < frame["command"].size(); i++) {
      vec_command.push_back(frame["command"][i].asString());
    }
    for (unsigned int i = 0; i < frame["dependency"].size(); i++) {
      string source_dep = frame["dependency"][i]["source_dep"].asString();
      string version_dep = frame["dependency"][i]["version_dep"].asString();
      map_dependency[source_dep] = version_dep;
    }
    {
      vector<string> bin(4);
      bin[0] = frame["binary"]["url"].asString();
      bin[1] = frame["binary"]["checksum"]["md5"].asString();
      bin[2] = frame["binary"]["checksum"]["sha1"].asString();
      bin[3] = frame["binary"]["checksum"]["sha256"].asString();
      vec_binary.push_back(bin);
      bin.clear();
    }
  }
  read_file.close();

  string target_url = "/home/code" + vec_binary[0][0];
  // install depdence

  ofstream write_bash("command.sh");
  write_bash << "#!/bin/bash" << endl;
  write_bash << "set -x" << endl;
  write_bash << "cd /home && mkdir code && cd code" << endl;
  write_bash << "export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"
             << endl;
  map<string, string>::iterator it = map_dependency.begin();

  system("apt-get update");
  while (it != map_dependency.end()) {
    if (it->first == "rust") {
      string order =
          "curl --proto '=https' --tlsv1.2 -sSf "
          "https://sh.rustup.rs | sh -s -- -y  --no-modify-path";
      write_bash << order << endl;
      order = "source \"$HOME/.cargo/env\"";
      write_bash << order << endl;
      order = "rustup install " + it->second;
      write_bash << order << endl;
      order = "rustup default " + it->second;
      write_bash << order << endl;
      // order = "rustup target add x86_64-unknown-linux-musl";
      // write_bash<<order<<endl;
    } else if (it->first == "_goetc") {
      // TODO expand other can't install by apt-get
    } else {
      system("tput setf 4");
      cout << it->first << "  " << it->second << "   will be installed "
           << endl;
      system("tput setf 7");
      string order = "apt-get install -y " + it->first + "=" + it->second;
      system(order.c_str());
    }

    it++;
  }

  // excude command

  for (vector<string>::iterator it = vec_command.begin();
       it != vec_command.end(); it++) {
    write_bash << *it << endl;
  }
  string order =
      "cp -r  " + target_url + " /home/rebuild/mount/" + map_property["name"];
  write_bash << order << endl;
  write_bash << "md5sum " << target_url << " >> /home/code/checksum" << endl;
  write_bash << "shasum " << target_url << " >> /home/code/checksum" << endl;
  write_bash << "sha256sum " << target_url << " >> /home/code/checksum" << endl;
  write_bash.close();
  system("bash command.sh");
  ifstream read_checksum("/home/code/checksum");
  if (!read_checksum.is_open()) {
    cerr << "failure to open check  file!" << endl;
    return false;
  }

  string line, address;
  vector<string> checksum_result(checksum_number);
  size_t i = 0;
  while (getline(read_checksum, line)) {
    /* code */
    stringstream ss;
    ss << line;
    ss >> checksum_result[i++] >> address;
    ss.clear();
  }
  map_property["md5sum"] = checksum_result[0];
  map_property["shasum"] = checksum_result[1];
  map_property["sha256sum"] = checksum_result[2];

  read_checksum.close();
  cout << "shasum is  " << map_property["shasum"] << endl;
  cout << "sha256sum is  " << map_property["sha256sum"] << endl;
  cout << "md5sum is  " << map_property["md5sum"] << endl;

  if (map_property["md5sum"] == vec_binary[0][1]) {
    cout << "Congratulations, the checksums are identical. This is a "
            "repeatable build."
         << endl;
  } else {
    cout << "Sorry for the checksum inconsistency , this is not a repeatable "
            "build"
         << endl;
  }

  // system("rm -rf  /home/code");
  // system("mv " +  target_url + " /home/rebuild/mount/" +
  // map_property["name"]);
  return true;
}

bool write_json_file() {
  // Json::Value data;
  Json::Value frame;
  Json::StyledWriter styledWriter;

  // frame["type"] = 10086;
  // data["username"] = "test";
  // frame["data"] = data;
  Json::Value config, name, version_gf, source, environment, dependency,
      command, binary, timestamp;
  Json::Value version;
  Json::Value sourceType, sourceLink;
  Json::Value architecture, kernel_version;
  Json::Value source_dep, version_dep;
  Json::Value url, checksum, md5, sha1, sha256;
  Json::Value bin, dep;

  config["version_gf"] = map_property["version_gf"];
  frame["config"] = config;
  frame["name"] = map_property["name"];
  frame["version"] = map_property["version"];
  source["source_type"] = map_property["source_type"];
  source["source_link"] = map_property["source_link"];
  frame["source"] = source;
  environment["architecture"] = "x86-64";
  environment["kernel_version"] = "5.0";
  frame["environment"] = environment;

  {
    map<string, string>::iterator it = map_dependency.begin();
    while (it != map_dependency.end()) {
      dep["source_dep"] = it->first;
      dep["version_dep"] = it->second;
      dependency.append(dep);
      it++;
    }
  }
  frame["dependency"] = dependency;
  {
    for (vector<string>::iterator it = vec_command.begin();
         it != vec_command.end(); it++) {
      command.append(*it);
    }
  }
  frame["command"] = command;
  bin["url"] = vec_binary[0][0];
  checksum["md5"] = map_property["md5sum"];
  checksum["sha1"] = map_property["shasum"];
  checksum["sha256"] = map_property["sha256sum"];
  bin["checksum"] = checksum;
  // binary.append(bin);
  frame["binary"] = bin;
  frame["timestamp"] = map_property["timestamp"];

  string result = styledWriter.write(frame);
  // write result  to a json file
  ofstream write_file("./mount/new.guanfu");
  write_file << result.c_str() << endl;
  // printf("%s \n", result.c_str());
  write_file.close();
  return true;
}

int main() {
  // parse json file
  read_json_file();

  // generate json file
  write_json_file();

  return 0;
}
