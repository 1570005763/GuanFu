fn main() -> shadow_rs::SdResult<()> {
    tonic_build::compile_protos("../protos/control.proto")?;
    tonic_build::compile_protos("../protos/query.proto")?;

    shadow_rs::new()
}
