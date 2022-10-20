fn main() -> shadow_rs::SdResult<()> {
    tonic_build::compile_protos("../protos/control.proto")?;

    shadow_rs::new()
}
