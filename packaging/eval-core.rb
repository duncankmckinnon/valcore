class EvalCore < Formula
  desc "Develop, improve, and run agentic evaluations locally"
  homepage "https://github.com/duncankmckinnon/eval-core"
  url "https://github.com/duncankmckinnon/eval-core/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_ON_RELEASE"
  license "Apache-2.0"

  depends_on "uv"

  def install
    libexec.install "packaging/eval-core.sh"
    (bin/"eval-core").write_env_script libexec/"eval-core.sh",
      EVALCORE_VERSION: version.to_s
  end

  test do
    assert_match "Usage", shell_output("#{bin}/eval-core --help")
  end
end
