use anyhow::{anyhow, Result};
use eventlog_rs::Eventlog;
use librats_rs::{TeeType, get_quote, verify_quote};
use serde::{Serialize, Deserialize};
use std::collections::HashMap;

use std::fs;

const EVENTLOG_INFO_PATH: &str = "/sys/firmware/acpi/tables/TDEL";
const EVENTLOG_DATA_PATH: &str = "/sys/firmware/acpi/tables/data/TDEL";

#[derive(Serialize, Deserialize)]
pub struct AttestationEv {
    pub quote: Vec<u8>,
    pub eventlog_info: Vec<u8>,
    pub eventlog_data: Vec<u8>,
}

pub async fn get_evidence(report_data: Vec<u8>) -> Result<AttestationEv> {
    let quote = get_quote(&report_data).await?;
    let eventlog_info = fs::read(EVENTLOG_INFO_PATH).map_err(|e| {
        anyhow!(
            "Read eventlog info path {} failed: {:?}",
            EVENTLOG_INFO_PATH,
            e
        )
    })?;
    let eventlog_data = fs::read(EVENTLOG_DATA_PATH).map_err(|e| {
        anyhow!(
            "Read eventlog data path {} failed: {:?}",
            EVENTLOG_DATA_PATH,
            e
        )
    })?;
    Ok(AttestationEv {
        quote,
        eventlog_info,
        eventlog_data,
    })
}

pub async fn verify_evidence(
    tee: &str,
    evidence: AttestationEv,
    report_data: &[u8],
) -> Result<Eventlog> {
    if tee != "tdx" {
        return Err(anyhow!("Unsupported TEE type"));
    }

    let parsed_claims = verify_quote(&evidence.quote, report_data, TeeType::TDX).await?;

    let event_log = Eventlog::try_from(evidence.eventlog_data.clone())?;
    
    let claims = serde_json::from_str::<serde_json::Value>(&parsed_claims)?;
    let mut rtmrs_from_quote: HashMap<u32, Vec<u8>> = HashMap::new();

    rtmrs_from_quote.insert(0 as u32, base64::decode(claims["rtmr0"].to_string())?);
    rtmrs_from_quote.insert(1 as u32, base64::decode(claims["rtmr1"].to_string())?);
    rtmrs_from_quote.insert(2 as u32, base64::decode(claims["rtmr2"].to_string())?);
    rtmrs_from_quote.insert(3 as u32, base64::decode(claims["rtmr3"].to_string())?);

    let rtmrs_from_eventlog = event_log.replay_measurement_regiestry();

    for rtmr_index in 0..4 {
        let reference_rtmr = rtmrs_from_quote.get(&rtmr_index).ok_or(anyhow!("Internal Error: get rtmr reference value failed"))?;
        let eventlog_rtmr = rtmrs_from_eventlog.get(&rtmr_index).ok_or(anyhow!("Internal Error: get rtmr value failed"))?;

        if reference_rtmr != eventlog_rtmr {
            return Err(anyhow!("RTMR value not matched. Maybe the eventlog has been tampered!!!"));
        }
    }

    Ok(event_log)
}