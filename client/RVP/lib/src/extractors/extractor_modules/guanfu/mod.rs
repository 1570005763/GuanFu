// Copyright (c) 2022 Alibaba Cloud
//
// SPDX-License-Identifier: Apache-2.0
//


///Defining the format of Provenance
use serde::{Serialize, Deserialize};
use std::fs;
use std::io::prelude::*;
use std::process::Command;
use anyhow::{ Result};
use chrono::{TimeZone, Utc};
#[derive(Serialize, Deserialize, Debug)]
pub struct Dependency
{
    source_dep: String,
    version_dep:String,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Config
{
    version_gf: String
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Environment
{
    architecture:String,
    kernel_version:String
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Source
{
    source_link: String,
    source_type: String
}


#[derive(Serialize, Deserialize, Debug)]
pub struct Checksum
{
    md5: String,
    sha1: String,
    sha256: String
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Binary
{
    checksum: Checksum,
    url: String
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Provenance
{
    version: String,
    timestamp: String,
    name:String,
    command:Vec<String>,
    dependency:Vec<Dependency>,
    config:Config,
    binary:Binary,
    environment:Environment,
    source:Source
}

#[derive(Serialize, Deserialize, Debug)]
pub struct People
{
    name: String,
    sex:String
}

use crate::ReferenceValue;
use super::Extractor;





pub struct GuanFuExtractor;
impl GuanFuExtractor {
    pub fn new() -> Self {
        GuanFuExtractor
    }
}





impl Extractor for GuanFuExtractor {
    // Verify and extract from the provenance.
    // Used to firstly parse provenance, and then verify it.
    // If the verification passes, extract relative 
    // reference value from it.
    // Input parameter: provenance from Message
    // Return value: ReferenceValue
    fn verify_and_extract(&self, provenance: &str) -> Result<ReferenceValue> {
        let payload: Provenance = serde_json::from_str(&provenance).unwrap();
        let _payload_md5 = payload.binary.checksum.md5;
        let _payload_sha1 = payload.binary.checksum.sha1;
        let _payload_sha256 = payload.binary.checksum.sha256;

        // 放置在 mount 目录
        let mut provenance_file = fs::File::create("./guanfu-rs/mount/old.guanfu").unwrap();
        provenance_file.write(provenance.as_bytes()).unwrap();

      //  调用一下bash 程序
        let output = Command::new("bash")
                    .arg("./guanfu-rs/build.sh")
                    .output()
                    .expect("Execution error, please re-check if the startup file exists");
        let _result = output.status;
        println!("{:?}",_result);
                    
      
       // 从里边来读一下
        let mut readfile = std::fs::File::open("./guanfu-rs/mount/new.guanfu").expect("请确定重复构建是否执行完成");
        let mut contents = String::new();
        readfile.read_to_string(&mut contents).unwrap();
        let unserialized:Provenance = serde_json::from_str(&contents).unwrap();



        let  rv = ReferenceValue::new()
            .set_name("kata-agent")
            .set_version("0.1")
            .set_expired(Utc.ymd(1970, 1, 1).and_hms(0, 0, 0))
            .add_hash_value("sha256".to_string(),unserialized.binary.checksum.sha256.to_string());
        Ok(rv)
            
    }
}




#[cfg(test)]
pub mod test {

    // Helps to generate a reference value.
    use serial_test::serial;
    use super::GuanFuExtractor;
    use super::{Provenance,Checksum,Binary,Config,Dependency,Environment,Source};
    use crate::{extractors::extractor_modules::Extractor, ReferenceValue};
    use chrono::{TimeZone, Utc};

    pub fn generate_guanfu_provenance() -> String {

        let che = Checksum{md5:"94114cfeea5deca136ebcb7263805e2e".to_string(),sha1:"d0c1db4ec2de38a2cd908a73dac7c87ded8b9ff9".to_string(),sha256:"9ca442bb48a1b6ed4409a18e95e519e48b1da7da34a9319271d959cabdb06706".to_string() };
        let bin = Binary {checksum:che,url:"/kata-containers/src/agent/target/x86_64-unknown-linux-musl/release/kata-agent".to_string()};
        let com = vec!{ "rustup target add x86_64-unknown-linux-musl".to_string(),
        "git clone https://github.com/kata-containers/kata-containers.git".to_string(),
        "cd  kata-containers/src/agent".to_string(),
        "git checkout 051dabb0fef2a85e329cb5d7f61d34716d9549b2".to_string(),
        
        "cargo fetch".to_string(),
        "dettrace --base-env host -- make".to_string()};
        let con = Config {version_gf:"0.1".to_string()};
        let dep1 = Dependency {source_dep:"make".to_string(),version_dep:"4.1-9.1ubuntu1".to_string()};
        let dep2 = Dependency {source_dep:"rust".to_string(),version_dep:"1.63.0".to_string()};
        let dep3 = Dependency {source_dep:"git".to_string(),version_dep:"1:2.17.1-1ubuntu0.12".to_string()};
        let dep4 = Dependency {source_dep:"curl".to_string(),version_dep:"7.58.0-2ubuntu3.19".to_string()};
        let dep5 = Dependency {source_dep:"libseccomp-dev".to_string(),version_dep:"2.5.1-1ubuntu1~18.04.2".to_string()};
        let dep6 = Dependency {source_dep:"gcc".to_string(),version_dep:"4:7.4.0-1ubuntu2.3".to_string()};
    
        let mut dep = Vec::new();
        dep.push(dep1);dep.push(dep2);dep.push(dep3);dep.push(dep4);dep.push(dep5);dep.push(dep6);
    
    
        let env = Environment{architecture:"x86-64".to_string(), kernel_version:"5.0".to_string()};
        let sou = Source{source_link:"https://github.com/kata-containers/kata-containers.git".to_string(),source_type:"git".to_string()};
    
        let buildinfo = Provenance{version:"1.0".to_string(),
                                timestamp:"1234567890".to_string(),
                                name:"kata-agent".to_string(),
                                source:sou,
                                environment:env,
                                dependency:dep,
                                config:con,
                                command:com,
                                binary:bin    
                            };
    
        let serialized = serde_json::to_string(&buildinfo).unwrap();
        serialized
    }


    #[test]
    #[serial]
    fn guanfu_extractor()
    {
        let e = GuanFuExtractor::new();
        let rv  = ReferenceValue::new()
            .set_name("kata-agent")
            .set_version("0.1")
            .set_expired(Utc.ymd(1970, 1, 1).and_hms(0, 0, 0))
            .add_hash_value("sha256".to_string(),"9ca442bb48a1b6ed4409a18e95e519e48b1da7da34a9319271d959cabdb06706".to_string());

        let provenance = generate_guanfu_provenance();
        let res = e.verify_and_extract(&provenance).unwrap();
        assert_eq!(res,rv);
    }
  
}
