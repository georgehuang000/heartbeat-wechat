import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import "./styles.css";
import "./heart.css";
import "./refinements.css";
import "./final.css";
import "./portrait-size.css";
import "./connected.css";
import "./pursuit.css";
import "./glass-theme.css";
import "./settings.css";

class InterfaceBoundary extends React.Component {
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){return this.state.failed?<p style={{padding:24,color:'#934b6c'}}>界面未能打开，请重启心动微信。</p>:this.props.children;}
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <InterfaceBoundary><App /></InterfaceBoundary>
  </React.StrictMode>,
);
