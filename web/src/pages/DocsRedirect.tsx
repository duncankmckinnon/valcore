import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { websiteDocsUrl } from "../docsLinks";

export default function DocsRedirect(): JSX.Element {
  const { slug } = useParams();
  const destination = websiteDocsUrl(slug);

  useEffect(() => {
    window.location.replace(destination);
  }, [destination]);

  return (
    <section>
      <PageHeader
        title="Documentation has moved"
        description="Valcore documentation now has one canonical home on e-valcore.com."
      />
      <a className="btn btn-primary" href={destination}>
        Continue to documentation
      </a>
    </section>
  );
}
