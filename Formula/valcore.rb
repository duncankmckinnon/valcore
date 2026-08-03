class Valcore < Formula
  desc "Develop, improve, and run agentic evaluations locally"
  homepage "https://github.com/duncankmckinnon/valcore"
  url "https://files.pythonhosted.org/packages/10/df/9f28157154c3535d06a2d5c3d48d83d759173e952c05873b7ff7740e57fa/valcore-0.0.1.tar.gz"
  sha256 "90716444fd525676b066eb0c961f9a7b1f4312d388ec890dba82b860eaf3d24f"
  license "Apache-2.0"

  depends_on "uv"

  def install
    libexec.install "packaging/valcore.sh"
    (bin/"valcore").write_env_script libexec/"valcore.sh",
      VALCORE_VERSION: version.to_s
  end

  test do
    assert_match "Usage", shell_output("#{bin}/valcore --help")
  end
end
